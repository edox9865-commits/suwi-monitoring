# -*- coding: utf-8 -*-
"""Firestore 연동(REST) - 앱(Flutter)과 동일한 측정수위 데이터를 공유한다.

- 프로젝트: suwi-monitor-ido
- 컬렉션: stations/{지점명}, 필드 stages = [측정수위 오름차순]
- 보안규칙이 개방형이므로 웹 API 키만으로 읽기/쓰기가 가능하다
  (Flutter 앱과 동일한 방식 → 실시간 동기화).
"""

import urllib.parse
import requests

PROJECT = "suwi-monitor-ido"
API_KEY = "AIzaSyCbJFItudyhIH5ot3LMLIgep8zLHGmD9Dc"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"


def _num(v):
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "integerValue" in v:
        return float(v["integerValue"])
    return None


def get_all_stages(timeout: int = 12) -> dict:
    """모든 지점의 측정수위(stages) 목록을 한 번에 읽는다. 실패 시 빈 dict."""
    url = f"{BASE}/stations?pageSize=200&key={API_KEY}"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return {}
    result = {}
    for doc in payload.get("documents", []):
        name = doc["name"].split("/")[-1]
        vals = (
            doc.get("fields", {})
            .get("stages", {})
            .get("arrayValue", {})
            .get("values", [])
        )
        stages = [_num(v) for v in vals]
        stages = sorted(x for x in stages if x is not None)
        result[name] = stages
    return result


def set_stages(name: str, stages, timeout: int = 12) -> bool:
    """지점의 측정수위 목록 전체를 저장(덮어쓰기)한다."""
    stages = sorted(float(s) for s in stages)
    doc = {
        "fields": {
            "stages": {
                "arrayValue": {
                    "values": [{"doubleValue": float(s)} for s in stages]
                }
            }
        }
    }
    enc = urllib.parse.quote(name, safe="")
    # updateMask=stages → 이 필드만 갱신하고 rt(실시간) 등 다른 필드는 보존한다.
    url = f"{BASE}/stations/{enc}?key={API_KEY}&updateMask.fieldPaths=stages"
    try:
        r = requests.patch(url, json=doc, timeout=timeout)
        return r.status_code == 200 and "name" in r.json()
    except Exception:
        return False


def set_realtime(name: str, points, updated: str, timeout: int = 15) -> bool:
    """지점의 실시간 수위 시계열을 저장한다.
    points: [(datetime, wl), ...]  /  updated: 수집시각 문자열(KST).
    한국망(맥·GitHub Actions 등)에서 HRFCO로 받아 여기에 넣으면,
    해외 클라우드(웹)는 이 값을 읽기만 하면 된다(HRFCO 직접호출 불가 우회)."""
    pts = []
    for dt, wl in points:
        pts.append({
            "mapValue": {
                "fields": {
                    "t": {"stringValue": dt.strftime("%Y%m%d%H%M")},
                    "wl": {"doubleValue": float(wl)},
                }
            }
        })
    doc = {
        "fields": {
            "rt": {
                "mapValue": {
                    "fields": {
                        "updated": {"stringValue": updated},
                        "points": {"arrayValue": {"values": pts}},
                    }
                }
            }
        }
    }
    enc = urllib.parse.quote(name, safe="")
    url = f"{BASE}/stations/{enc}?key={API_KEY}&updateMask.fieldPaths=rt"
    try:
        r = requests.patch(url, json=doc, timeout=timeout)
        return r.status_code == 200 and "name" in r.json()
    except Exception:
        return False


def get_all_realtime(timeout: int = 12) -> dict:
    """모든 지점의 실시간 시계열을 읽는다.
    반환: {지점명: {"updated": str, "points": [(datetime, float), ...]}}. 실패 시 빈 dict."""
    from datetime import datetime
    url = f"{BASE}/stations?pageSize=200&key={API_KEY}"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return {}
    result = {}
    for doc in payload.get("documents", []):
        name = doc["name"].split("/")[-1]
        rt = doc.get("fields", {}).get("rt", {}).get("mapValue", {}).get("fields", {})
        if not rt:
            continue
        updated = rt.get("updated", {}).get("stringValue", "")
        vals = rt.get("points", {}).get("arrayValue", {}).get("values", [])
        pts = []
        for v in vals:
            f = v.get("mapValue", {}).get("fields", {})
            t = f.get("t", {}).get("stringValue", "")
            wl = _num(f.get("wl", {}))
            if not t or wl is None:
                continue
            try:
                dt = datetime.strptime(t[:12], "%Y%m%d%H%M") if len(t) >= 12 else datetime.strptime(t[:10], "%Y%m%d%H")
            except ValueError:
                continue
            pts.append((dt, wl))
        pts.sort(key=lambda x: x[0])
        result[name] = {"updated": updated, "points": pts}
    return result
