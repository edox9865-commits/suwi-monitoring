# -*- coding: utf-8 -*-
"""실시간 수위 수집기 — 한국망(맥/GitHub Actions)에서 실행.

해외 클라우드(Streamlit)는 HRFCO에 직접 연결이 안 되므로(ConnectTimeout),
한국망에서 HRFCO 수위를 받아 Firestore에 저장한다. 웹은 Firestore만 읽는다.

- 실행: python collector.py [조회시간(기본 24)]
- 주기 실행: GitHub Actions(cron) 또는 맥 launchd
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from hrfco_api import _fetch_hrfco, _fetch_wamis
from firestore_rest import set_realtime

KST = timezone(timedelta(hours=9))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATION_MAP_PATH = os.path.join(SCRIPT_DIR, "station_map.json")


def main(hours: int = 24) -> int:
    with open(STATION_MAP_PATH, encoding="utf-8") as f:
        station_map = json.load(f)

    updated = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    def work(item):
        name, info = item
        code = info.get("hrfco_code")
        if not code:
            return name, "코드없음", 0
        # 앱과 동일한 전략: WAMIS 우선(14지점 안정, 죽동교/보성교 등 HRFCO가 0으로 주는 지점 포함)
        # → 실패/빈자료/전부 0이면 HRFCO 백업. 수집기는 한국망이라 WAMIS(8080) 접속 가능.
        df = pd.DataFrame(columns=["datetime", "wl"])
        src = ""
        try:
            w = _fetch_wamis(code, hours)
            if not w.empty and not (w["wl"] == 0.0).all():
                df, src = w, "WAMIS"
        except Exception:
            pass
        if df.empty:
            try:
                h = _fetch_hrfco(code, hours)
                if not h.empty:
                    df, src = h, "HRFCO"
            except Exception as e:
                return name, f"조회오류:{type(e).__name__}", 0
        if df.empty:
            return name, "빈자료", 0
        points = list(zip(df["datetime"], df["wl"]))
        ok = set_realtime(name, points, updated)
        return name, (f"저장({src})" if ok else "저장실패"), len(points)

    items = list(station_map.items())
    ok_cnt = 0
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(items)))) as ex:
        for name, status, n in ex.map(work, items):
            if status.startswith("저장("):
                ok_cnt += 1
            print(f"{name:8s} {status:12s} {n}건")

    print(f"\n수집완료 {updated} KST · 성공 {ok_cnt}/{len(items)}개")
    return 0 if ok_cnt > 0 else 1


if __name__ == "__main__":
    hrs = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    sys.exit(main(hrs))
