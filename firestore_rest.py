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
    url = f"{BASE}/stations/{enc}?key={API_KEY}"
    try:
        r = requests.patch(url, json=doc, timeout=timeout)
        return r.status_code == 200 and "name" in r.json()
    except Exception:
        return False
