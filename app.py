# -*- coding: utf-8 -*-
"""수위 모니터링 대시보드
- 한강홍수통제소 Open API로 지점별 최근 수위자료 조회
- DB_표준양식_*.xlsm 의 기존 측정 수위대(H범위)를 함께 표시
"""

import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from hrfco_api import fetch_hourly_waterlevel
from db_reader import get_measured_stage_range, get_file_mtime, get_base_dir
from firestore_rest import get_all_stages, set_stages

WAMIS_BASE = "http://www.wamis.go.kr:8080/wamis/openapi/wkw"
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """한국시간(KST) 기준 현재시각(naive). 앱은 한국 폰이라 항상 KST를 쓰는데,
    웹 클라우드는 UTC라 그대로 두면 조회 구간이 9시간 어긋난다."""
    return datetime.now(KST).replace(tzinfo=None)


def fetch_wamis(code: str, hours: int) -> pd.DataFrame:
    """WAMIS Open API에서 시간단위 수위자료 조회."""
    end = now_kst()
    start = end - timedelta(hours=hours)
    url = f"{WAMIS_BASE}/wl_hrdata?obscd={code}&startdt={start.strftime('%Y%m%d')}&enddt={end.strftime('%Y%m%d')}&output=json"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    payload = r.json()
    if payload.get("result", {}).get("code") != "success":
        return pd.DataFrame(columns=["datetime", "wl"])
    rows = []
    for item in payload.get("list", []):
        ymdh = item.get("ymdh", "")
        wl = item.get("wl")
        if not ymdh or wl in (None, "", " "):
            continue
        try:
            dt = datetime.strptime(ymdh[:10], "%Y%m%d%H")
            wl_val = float(wl)
        except (ValueError, TypeError):
            continue
        if start <= dt <= end:
            rows.append({"datetime": dt, "wl": wl_val})
    if not rows:
        return pd.DataFrame(columns=["datetime", "wl"])
    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATION_MAP_PATH = os.path.join(SCRIPT_DIR, "station_map.json")
DB_FOLDER_PATH = os.path.join(get_base_dir(), "DB")

st.set_page_config(
    page_title="수위 모니터링 대시보드",
    page_icon="💧",
    layout="wide",
    menu_items={},
)

import streamlit.components.v1 as components

components.html(
    """
    <script>
    try {
        const doc = window.parent.document;
        if (!doc.querySelector('meta[name="google"]')) {
            const meta = doc.createElement('meta');
            meta.name = 'google';
            meta.content = 'notranslate';
            doc.head.appendChild(meta);
        }
        doc.documentElement.setAttribute('translate', 'no');
        doc.documentElement.lang = 'ko';
    } catch (e) {}
    </script>
    """,
    height=0,
)

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header [data-testid="stToolbar"] button[kind="header"] {visibility: hidden;}
    [data-testid="stToolbarActions"] {visibility: hidden;}
    [data-testid="collapsedControl"] {visibility: visible !important;}

    .block-container { padding-top: 1.2rem !important; }
    section[data-testid="stSidebar"] .block-container { padding-top: 1.2rem !important; }
    div[data-testid="stIFrame"] { display: none; }

    .app-header {
        background: linear-gradient(135deg, #1e6fd9 0%, #21c6c2 100%);
        padding: 20px 32px;
        border-radius: 16px;
        color: white;
        margin-bottom: 18px;
        box-shadow: 0 6px 18px rgba(30, 111, 217, 0.25);
    }
    .app-header h1 { margin: 0; font-size: 30px; }
    .app-header p { margin: 6px 0 0 0; opacity: 0.92; font-size: 15px; }

    .kpi-card {
        border-radius: 14px;
        padding: 16px 18px;
        background: white;
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
        border-left: 6px solid #2563eb;
    }
    .kpi-card.danger { border-left-color: #e3492a; background: #fff5f3; }
    .kpi-card.ok { border-left-color: #19a974; }
    .kpi-card.muted { border-left-color: #9ca3af; background: #f8f9fa; }
    .kpi-name { font-size: 15px; font-weight: 700; color: #1f2937; margin-bottom: 6px;}
    .kpi-value { font-size: 24px; font-weight: 800; color: #111827; }
    .kpi-sub { font-size: 12.5px; color: #6b7280; margin-top: 4px;}
    .badge {
        display: inline-block; padding: 2px 9px; border-radius: 999px;
        font-size: 11.5px; font-weight: 700; margin-top: 6px;
    }
    .badge.danger { background: #ffe1da; color: #b3331c; }
    .badge.ok { background: #d9f7ea; color: #0f7a4f; }
    .badge.muted { background: #eceff1; color: #5f6b76; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=120)
def load_waterlevel_bulk(codes: tuple, hours: int):
    """선택된 모든 지점의 수위자료를 동시에(병렬) 조회한다.
    HRFCO(https/443) 우선 → 실패/0값이면 WAMIS(8080)로 자동 대체.
    반환: {code: (df, err_or_source)}  — 진단을 위해 실제 사용된 소스/오류를 함께 담는다."""
    results = {}

    def _fetch(code):
        errors = []
        # 1단계: HRFCO(https/443) 우선 — 해외 클라우드 방화벽을 통과할 확률이 높다.
        try:
            df = fetch_hourly_waterlevel(code, hours)
            if not df.empty and not (df["wl"] == 0.0).all():
                return code, df, "HRFCO"
            errors.append("HRFCO 빈자료/0값")
        except Exception as e:
            errors.append(f"HRFCO 오류: {type(e).__name__}")
        # 2단계: 최후 수단으로 WAMIS(포트 8080) 직접 시도 (국내망에서 유효)
        try:
            wdf = fetch_wamis(code, hours)
            if not wdf.empty:
                return code, wdf, "WAMIS"
            errors.append("WAMIS 빈자료")
        except Exception as e:
            errors.append(f"WAMIS 오류: {type(e).__name__}")
        return code, pd.DataFrame(columns=["datetime", "wl"]), " / ".join(errors)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(codes)))) as ex:
        for code, df, src in ex.map(_fetch, codes):
            results[code] = (df, src)
    return results


@st.cache_data
def load_measured_range(db_file: str, mtime: float):
    # mtime이 캐시 키에 포함되어, 엑셀 파일이 갱신되면 자동으로 다시 읽힌다
    return get_measured_stage_range(db_file)


def load_station_map():
    with open(STATION_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=60)
def load_all_stages_fs():
    """앱(Flutter)과 공유하는 Firestore의 측정수위 데이터를 읽는다."""
    return get_all_stages()


def build_measured(stages):
    """측정수위 목록으로 h_min/h_max/오름차순 구조를 만든다."""
    if not stages:
        return None
    s = sorted(float(x) for x in stages)
    return {"h_min": s[0], "h_max": s[-1], "stages_asc": s, "n": len(s)}


station_map = load_station_map()
fs_stages = load_all_stages_fs()

st.markdown(
    """
    <div class="app-header">
        <h1>💧 측정지점 수위 모니터링</h1>
        <p>한강홍수통제소 Open API 실시간 수위 자료와 기존 측정 수위대(DB)를 한눈에 비교합니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

all_names = list(station_map.keys())

with st.sidebar:
    st.markdown("### ⚙️ 조회 설정")
    hours = st.slider("조회 기간(시간)", min_value=6, max_value=72, value=24, step=6)

    st.markdown("### 📍 지점 선택")
    c1, c2 = st.columns(2)
    if c1.button("전체선택", use_container_width=True):
        for nm in all_names:
            st.session_state[f"chk_{nm}"] = True
    if c2.button("선택해제", use_container_width=True):
        for nm in all_names:
            st.session_state[f"chk_{nm}"] = False

    selected = []
    for nm in all_names:
        checked = st.checkbox(nm, value=st.session_state.get(f"chk_{nm}", True), key=f"chk_{nm}")
        if checked:
            selected.append(nm)

    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("### ✏️ 측정수위 관리")
    st.caption("여기서 추가·삭제한 값은 앱(Flutter)과 실시간으로 동기화됩니다.")
    manage_name = st.selectbox("지점 선택", all_names, key="manage_target")
    cur_stages = sorted(fs_stages.get(manage_name, []))

    st.markdown(f"**현재 측정수위 {len(cur_stages)}개** (오름차순, m)")
    if cur_stages:
        for i, val in enumerate(cur_stages):
            rc1, rc2 = st.columns([4, 1])
            rc1.markdown(f"{i + 1}. **{val:.3f}** m")
            if rc2.button("🗑", key=f"del_{manage_name}_{i}", help="삭제"):
                new_list = cur_stages[:i] + cur_stages[i + 1:]
                if set_stages(manage_name, new_list):
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("삭제 실패(네트워크). 다시 시도하세요.")
    else:
        st.caption("등록된 측정수위가 없습니다.")

    add_val = st.number_input(
        "측정수위 추가 (m)", min_value=0.0, max_value=100.0,
        value=0.0, step=0.01, format="%.3f", key=f"add_{manage_name}",
    )
    if st.button("➕ 추가", use_container_width=True, type="primary"):
        if add_val > 0:
            if set_stages(manage_name, cur_stages + [add_val]):
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("추가 실패(네트워크). 다시 시도하세요.")
        else:
            st.warning("0보다 큰 값을 입력하세요.")

if not selected:
    st.info("좌측에서 지점을 선택하세요.")
    st.stop()

summary_rows = []
station_results = {}

codes_to_fetch = tuple(sorted({
    station_map[name]["hrfco_code"] for name in selected if station_map[name].get("hrfco_code")
}))

with st.spinner("수위 자료를 불러오는 중입니다..."):
    bulk_results = load_waterlevel_bulk(codes_to_fetch, hours) if codes_to_fetch else {}

# ── 실시간 조회 진단 (클라우드에서 실제로 무엇을 겪는지 눈으로 확인) ──
with st.expander("🩺 실시간 조회 진단 (문제 확인용)", expanded=False):
    st.caption(f"서버 기준시각(KST): {now_kst():%Y-%m-%d %H:%M}  ·  조회기간 {hours}h")
    code_to_name = {station_map[nm].get("hrfco_code"): nm for nm in selected}
    diag_rows = []
    for c in codes_to_fetch:
        df_c, src_c = bulk_results.get(c, (pd.DataFrame(), "미조회"))
        n = 0 if df_c is None or df_c.empty else len(df_c)
        last_v = "-" if n == 0 else f"{df_c['wl'].iloc[-1]:.2f} m"
        last_t = "-" if n == 0 else f"{df_c['datetime'].iloc[-1]:%m-%d %H:%M}"
        diag_rows.append({
            "지점": code_to_name.get(c, "?"), "코드": c,
            "소스/상태": src_c, "건수": n, "최근값": last_v, "최근시각": last_t,
        })
    st.dataframe(pd.DataFrame(diag_rows), use_container_width=True, hide_index=True)

for name in selected:
    info = station_map[name]
    code = info.get("hrfco_code")
    db_file = info.get("db_file")

    wl_df = pd.DataFrame()
    err = None
    if code:
        wl_df, err = bulk_results.get(code, (pd.DataFrame(), "조회 실패"))
    else:
        err = "국가관측망 코드 미확인 지점"

    measured = None
    stages_fs = fs_stages.get(name)
    if stages_fs:
        measured = build_measured(stages_fs)
    elif db_file:
        # 폴백: Firestore에 자료가 없으면 기존 엑셀/번들 자료 사용
        mtime = get_file_mtime(db_file)
        measured = load_measured_range(db_file, mtime)

    station_results[name] = {"wl_df": wl_df, "measured": measured, "err": err}

    cur = wl_df["wl"].iloc[-1] if not wl_df.empty else None
    avg = wl_df["wl"].mean() if not wl_df.empty else None
    hmin = measured["h_min"] if measured else None
    hmax = measured["h_max"] if measured else None

    out_of_range = None
    if cur is not None and hmin is not None and hmax is not None:
        out_of_range = not (hmin <= cur <= hmax)

    stages_asc = measured.get("stages_asc") if measured else None

    summary_rows.append({
        "name": name, "cur": cur, "avg": avg,
        "hmin": hmin, "hmax": hmax,
        "out_of_range": out_of_range, "err": err,
        "stages_asc": stages_asc,
    })

st.markdown("#### 📊 지점별 요약")

ncards = 4
for i in range(0, len(summary_rows), ncards):
    cols = st.columns(ncards)
    for col, row in zip(cols, summary_rows[i:i + ncards]):
        if row["cur"] is None:
            css = "muted"
            badge = '<span class="badge muted">자료없음</span>'
            value_html = "-"
        elif row["out_of_range"]:
            css = "danger"
            badge = '<span class="badge danger">기존측정수위 범위 밖</span>'
            value_html = f"{row['cur']:.2f} m"
        else:
            css = "ok"
            badge = '<span class="badge ok">기존측정수위 범위 내</span>'
            value_html = f"{row['cur']:.2f} m"

        if row["stages_asc"]:
            vals = ", ".join(f"{v:.2f}" for v in row["stages_asc"])
            range_txt = f"기존측정수위(오름차순): {vals} m"
        else:
            range_txt = "기존측정수위 자료 없음"
        avg_txt = f"{hours}h 평균 {row['avg']:.2f}m" if row["avg"] is not None else ""

        col.markdown(
            f"""
            <div class="kpi-card {css}">
                <div class="kpi-name">{row['name']}</div>
                <div class="kpi-value">{value_html}</div>
                <div class="kpi-sub">{avg_txt}</div>
                <div class="kpi-sub">{range_txt}</div>
                {badge}
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.write("")

st.markdown("#### 📈 지점별 수위 변화 그래프")

n = len(selected)
ncols = 2 if n > 1 else 1
nrows = (n + ncols - 1) // ncols

v_spacing = min(0.05, 0.9 / max(nrows - 1, 1)) if nrows > 1 else 0.15

fig = make_subplots(
    rows=nrows, cols=ncols,
    subplot_titles=selected,
    vertical_spacing=v_spacing,
)

LINE_COLOR = "#1e6fd9"
AVG_COLOR = "#f59e0b"
BAND_COLOR = "#19a974"

for idx, name in enumerate(selected):
    row = idx // ncols + 1
    col = idx % ncols + 1
    res = station_results[name]
    wl_df = res["wl_df"]
    measured = res["measured"]

    if not wl_df.empty:
        fig.add_trace(
            go.Scatter(
                x=wl_df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(), y=wl_df["wl"],
                mode="lines+markers", name=f"{name} 실측수위",
                line=dict(color=LINE_COLOR, width=2.5),
                marker=dict(size=5, color=LINE_COLOR),
                fill="tozeroy", fillcolor="rgba(30,111,217,0.08)",
                showlegend=False,
            ),
            row=row, col=col,
        )
        avg_val = wl_df["wl"].mean()
        fig.add_hline(
            y=avg_val, line=dict(color=AVG_COLOR, dash="dash", width=1.6),
            row=row, col=col,
            annotation_text=f"평균 {avg_val:.2f}m", annotation_position="top left",
            annotation_font_color=AVG_COLOR,
        )
    else:
        # row/col 지정 시 plotly가 해당 subplot 축으로 자동 매핑한다.
        # (구버전처럼 "x1 domain"으로 직접 지정하면 최신 plotly에서 오류)
        fig.add_annotation(
            text="자료없음", row=row, col=col,
            x=0.5, y=0.5, xref="x domain", yref="y domain",
            showarrow=False, font=dict(size=14, color="#9ca3af"),
        )

    if measured:
        fig.add_hrect(
            y0=measured["h_min"], y1=measured["h_max"],
            line_width=0, fillcolor=BAND_COLOR, opacity=0.15,
            row=row, col=col,
        )

    # y축을 실측수위 변동폭에 맞춰 좁게 설정 (기존측정 음영이 넓어도 변동이 잘 보이도록)
    if not wl_df.empty:
        wl_min, wl_max = wl_df["wl"].min(), wl_df["wl"].max()
        pad = max((wl_max - wl_min) * 0.6, 0.05)
        fig.update_yaxes(range=[wl_min - pad, wl_max + pad], row=row, col=col)

fig.update_layout(
    height=420 * nrows, margin=dict(t=50, b=10),
    plot_bgcolor="white", paper_bgcolor="white",
    font=dict(family="sans serif", size=12, color="#1f2937"),
)
fig.update_xaxes(showgrid=True, gridcolor="#eef2f6", type="date", tickformat="%m-%d %H:%M")
fig.update_yaxes(showgrid=True, gridcolor="#eef2f6")

st.plotly_chart(fig, use_container_width=True)

st.caption(
    "초록색 음영 = 기존 측정 수위대(DB 엑셀의 최소~최대 측정수위). "
    "음영을 벗어난 실측수위는 기존에 측정해보지 못한 구간입니다. "
    "단, DB 수위와 실시간 자료가 동일한 기준점(0점)을 쓰는지는 별도 확인이 필요합니다."
)
