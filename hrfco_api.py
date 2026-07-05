# -*- coding: utf-8 -*-
"""수위자료 조회 - 한강홍수통제소(HRFCO) Open API, 실패 시 WAMIS Open API 자동 대체"""

import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

AUTH_KEY = "8791AA48-CC32-4217-940F-BC80E7568A67"
HRFCO_BASE = f"https://api.hrfco.go.kr/{AUTH_KEY}/waterlevel"
WAMIS_BASE = "http://www.wamis.go.kr:8080/wamis/openapi/wkw"
KST = timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    """한국시간 기준 현재시각(naive). 앱은 한국 폰이라 KST, 웹 클라우드는 UTC이므로 통일한다."""
    return datetime.now(KST).replace(tzinfo=None)


def _fetch_hrfco(hrfco_code: str, hours: int) -> pd.DataFrame:
    """HRFCO Open API에서 시간단위 수위자료 조회."""
    end = _now_kst()
    start = end - timedelta(hours=hours)
    sy = start.strftime("%Y%m%d%H%M")
    ey = end.strftime("%Y%m%d%H%M")

    url = f"{HRFCO_BASE}/list/1H/{hrfco_code}/{sy}/{ey}.json"
    resp = requests.get(url, timeout=25)
    resp.raise_for_status()
    payload = resp.json()

    rows = []
    for item in payload.get("content", []):
        ymdhm = item.get("ymdhm")
        wl = item.get("wl")
        if not ymdhm or wl in (None, "", " "):
            continue
        try:
            wl_val = float(wl)
        except (TypeError, ValueError):
            continue
        ymdhm = ymdhm.strip()
        try:
            dt = datetime.strptime(ymdhm, "%Y%m%d%H") if len(ymdhm) == 10 else datetime.strptime(ymdhm[:12], "%Y%m%d%H%M")
        except ValueError:
            continue
        rows.append({"datetime": dt, "wl": wl_val})

    if not rows:
        return pd.DataFrame(columns=["datetime", "wl"])
    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)


def _fetch_wamis(hrfco_code: str, hours: int) -> pd.DataFrame:
    """WAMIS Open API에서 시간단위 수위자료 조회 (HRFCO 실패 시 대체)."""
    end = _now_kst()
    start = end - timedelta(hours=hours)

    # WAMIS는 날짜 단위 조회, 시작~종료 날짜 커버
    startdt = start.strftime("%Y%m%d")
    enddt = end.strftime("%Y%m%d")

    url = f"{WAMIS_BASE}/wl_hrdata?obscd={hrfco_code}&startdt={startdt}&enddt={enddt}&output=json"
    resp = requests.get(url, timeout=25)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("result", {}).get("code") != "success":
        return pd.DataFrame(columns=["datetime", "wl"])

    rows = []
    for item in payload.get("list", []):
        ymdh = item.get("ymdh", "")
        wl = item.get("wl")
        if not ymdh or wl in (None, "", " "):
            continue
        try:
            wl_val = float(wl)
            dt = datetime.strptime(ymdh[:10], "%Y%m%d%H")
        except (TypeError, ValueError):
            continue
        # 조회 범위(start~end)에 해당하는 것만 포함
        if start <= dt <= end:
            rows.append({"datetime": dt, "wl": wl_val})

    if not rows:
        return pd.DataFrame(columns=["datetime", "wl"])
    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)


def fetch_hourly_waterlevel(hrfco_code: str, hours: int = 24) -> pd.DataFrame:
    """시간단위 수위자료 조회.
    HRFCO Open API → 자료 없거나 전부 0이거나 연결오류면 WAMIS Open API로 자동 전환.
    Returns: DataFrame[datetime, wl] (시간 오름차순)
    """
    df = pd.DataFrame(columns=["datetime", "wl"])
    try:
        df = _fetch_hrfco(hrfco_code, hours)
    except Exception:
        pass  # HRFCO 연결 오류 → WAMIS로 대체

    # HRFCO 자료가 없거나 전부 0.00이면 WAMIS 시도
    if df.empty or (len(df) > 0 and (df["wl"] == 0.0).all()):
        try:
            wamis_df = _fetch_wamis(hrfco_code, hours)
            if not wamis_df.empty:
                return wamis_df
        except Exception:
            pass  # WAMIS도 실패하면 HRFCO 원본 반환

    return df
