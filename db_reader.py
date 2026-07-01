# -*- coding: utf-8 -*-
"""DB_표준양식_*.xlsm 파일에서 지점별 기존 측정 수위(H) 범위 추출.

DB 엑셀이 없는 환경(웹 배포 등)에서는 measured_ranges.json 을 폴백으로 사용한다.
"""

import json
import os
import sys

try:
    from excel_reader import read_db_file
except Exception:  # openpyxl 미설치 등 (웹 경량 배포)
    read_db_file = None


_RANGES_CACHE = None


def _load_ranges_json():
    """measured_ranges.json 을 한 번만 읽어 메모리에 캐시."""
    global _RANGES_CACHE
    if _RANGES_CACHE is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "measured_ranges.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _RANGES_CACHE = json.load(f)
        else:
            _RANGES_CACHE = {}
    return _RANGES_CACHE


def get_base_dir() -> str:
    """DB 폴더를 찾을 기준 경로.
    - exe로 패키징된 경우: exe가 있는 폴더 (배포 시 DB 폴더를 exe 옆에 둠)
    - 개발 중(원본 프로젝트 내에서 실행): 이 스크립트 폴더(수위모니터링) 옆에 DB가 있으면 그곳,
      없으면 상위 프로젝트 폴더(기존 34.수위-유량관계곡선식 개발 프로그램/DB)를 사용
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)

    this_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(this_dir, "DB")):
        return this_dir
    parent_dir = os.path.dirname(this_dir)
    if os.path.isdir(os.path.join(parent_dir, "DB")):
        return parent_dir
    return this_dir


DB_DIR = os.path.join(get_base_dir(), "DB")


def get_measured_stage_range(db_file: str):
    """db_file(파일명)에서 측정 수위(H)의 최소/최대값을 반환. 실패 시 None.

    DB 엑셀은 매달 측정자료가 추가되며 갱신되므로, 파일의 수정시각(mtime)이
    바뀔 때마다 다시 읽도록 호출 측(app.py)에서 mtime을 캐시 키에 포함시킨다.
    """
    filepath = os.path.join(DB_DIR, db_file)
    # 1) DB 엑셀이 있으면 엑셀에서 직접 추출 (로컬/맥앱)
    if read_db_file is not None and os.path.exists(filepath):
        try:
            result = read_db_file(filepath)
            df = result["data"]
            if not df.empty:
                stages = sorted(float(v) for v in df["stage"].tolist())
                return {
                    "h_min": float(df["stage"].min()),
                    "h_max": float(df["stage"].max()),
                    "n": len(df),
                    "stages_asc": stages,
                }
        except Exception:
            pass
    # 2) 엑셀이 없으면 measured_ranges.json 폴백 (웹 경량 배포)
    ranges = _load_ranges_json()
    return ranges.get(db_file)


def get_file_mtime(db_file: str) -> float:
    filepath = os.path.join(DB_DIR, db_file)
    if not os.path.exists(filepath):
        return 0.0
    return os.path.getmtime(filepath)
