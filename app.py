# 파일명: ddulsa200_app.py
# ============================================================================
#  떨사 200 (Ddulsa 200) — v2.0 "항법 계기판(Navigation Console)"
# ----------------------------------------------------------------------------
#  변경사항 (v1.0 → v2.0)
#
#  [작명] 모드 이름 전면 교체 — 오해를 낳던 '공격/방어' 폐기
#     · ⚔️ 공격(Offense) → 🛥️ 순항(Cruise)   : 추세 위, 6분할, 저노출, +2.5% 수확
#     · 🛡️ 방어(Safe)   → 🚨 구조(Rescue)   : 추세 아래, 4분할, 고노출⚠️, +0.01% 탈출
#     · 근거: '방어'가 실제로는 더 공격적이었다.
#             티어 크기 = op_base ÷ 분할수 이므로 4분할(구조) 티어가 6분할(순항)보다 크고,
#             최대 총노출도 구조 1.25×op > 순항 1.17×op 로 역전.
#             실측 평균 노출: 순항 22.0% vs 구조 32.6% (p95: 62.4% vs 87.2%).
#             즉 '구조'는 자본보존 모드가 아니라 '약세장 급반등 스캘핑' 모드다.
#     · 내부 키('Offense'/'Safe')는 그대로 유지 → 기존 CSV·로그·birth_mode 호환.
#       표시 레이어(MODE_META)만 교체했다.
#
#  [UI] 항법 계기판 컨셉으로 전면 개편
#     · 시그니처: '노출 게이지' — 순항/구조의 노출 역전을 화면에서 직접 보여준다.
#     · 색: 순항=청록(차분), 구조=앰버(경광등), 위험=적색(낙폭 경고 전용).
#     · 차트: 계기판 톤의 모던 스타일(불필요한 축·격자 제거, 등폭 숫자).
#
#  [문서] 전략 로직 탭 전면 확장 — 로버스트 검증 전체 수록
#     · 파라미터 민감도 / 워크포워드 / DSR·CSCV / 몬테카를로
#     · 실패한 개선 시도 7종 기록 (재시도 방지)
#     · 예상 반론 FAQ
#
#  ⚠️ 몬테카를로 핵심 경고 (자금관리 필독)
#     실현 MDD −29.3%는 재표본 분포의 상위 5% '행운'이다. 기대 MDD는 약 −47%.
#     P(MDD<−40%)=72% / P(MDD<−50%)=41% / P(MDD<−60%)=20%
#     → 자금관리는 −45~50% 낙폭을 전제로 설계할 것.
# ============================================================================
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import json
import re
import time
import plotly.graph_objects as go

try:
    from github import Github
    from io import StringIO
    _HAS_GH = True
except Exception:
    _HAS_GH = False
try:
    import gspread
    from google.oauth2.service_account import Credentials
    _HAS_GS = True
except Exception:
    _HAS_GS = False

st.set_page_config(page_title="떨사 200 · 항법 계기판", page_icon="🛥️", layout="wide")

# ============================================================================
#  디자인 토큰 — "항법 계기판"
#    hull   : 계기판 몸체 (배경)
#    panel  : 계기 카드
#    cruise : 순항 — 차분한 청록 (steady water)
#    rescue : 구조 — 경광등 앰버 (beacon)
#    alert  : 위험 — 적색 (낙폭 경고 전용, 남용 금지)
# ============================================================================
CRUISE = "#0E9384"   # teal — 순항
RESCUE = "#DB7706"   # amber — 구조
ALERT  = "#D1453B"   # red — 위험 경고
INK    = "#0F1B26"
MIST   = "#7C8FA3"

st.markdown(f"""
<style>
  @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css");
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');

  :root {{
    --cruise:{CRUISE}; --cruise-dim:#0E938414; --cruise-line:#0E938440;
    --rescue:{RESCUE}; --rescue-dim:#DB770614; --rescue-line:#DB770640;
    --alert:{ALERT};   --alert-dim:#D1453B14;
    --ink:{INK}; --mist:{MIST};
    --hull:#F7F9FA; --panel:#FFFFFF; --line:#E3E9ED; --line-2:#CFD8DF;
    --mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --ink:#E9EFF4; --mist:#8DA0B2;
      --hull:#0D151C; --panel:#141F29; --line:#22303C; --line-2:#2E404F;
      --cruise-dim:#0E93841F; --rescue-dim:#DB77061F; --alert-dim:#D1453B1F;
    }}
  }}

  html, body, [class*="css"] {{ font-family:'Pretendard',system-ui,sans-serif; }}
  .num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}

  /* ── masthead ─────────────────────────────── */
  .mast {{ border-bottom:2px solid var(--ink); padding:4px 0 14px; margin-bottom:20px; }}
  .mast .eyebrow {{ font-family:var(--mono); font-size:11px; letter-spacing:.18em;
    text-transform:uppercase; color:var(--mist); font-weight:600; }}
  .mast h1 {{ font-size:1.9rem; font-weight:800; letter-spacing:-.02em; margin:2px 0 2px; }}
  .mast .sub {{ color:var(--mist); font-size:.88rem; }}

  /* ── SIGNATURE: 모드 배너 + 노출 게이지 ────── */
  .bridge {{ border-radius:14px; padding:20px 22px; margin:6px 0 18px;
    border:1px solid var(--line); background:var(--panel); position:relative; overflow:hidden; }}
  .bridge.cruise {{ border-left:5px solid var(--cruise); background:
      linear-gradient(90deg, var(--cruise-dim) 0%, var(--panel) 55%); }}
  .bridge.rescue {{ border-left:5px solid var(--rescue); background:
      linear-gradient(90deg, var(--rescue-dim) 0%, var(--panel) 55%); }}
  .bridge-top {{ display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }}
  .bridge-name {{ font-size:1.45rem; font-weight:800; letter-spacing:-.01em; }}
  .bridge.cruise .bridge-name {{ color:var(--cruise); }}
  .bridge.rescue .bridge-name {{ color:var(--rescue); }}
  .bridge-en {{ font-family:var(--mono); font-size:.72rem; letter-spacing:.14em;
    text-transform:uppercase; color:var(--mist); font-weight:600; }}
  .bridge-why {{ color:var(--mist); font-size:.88rem; margin-top:2px; }}
  .bridge-rules {{ margin-top:12px; font-size:.86rem; color:var(--ink); }}
  .bridge-rules b {{ font-family:var(--mono); }}

  /* 노출 게이지 — 이 전략의 반직관(순항=저노출/구조=고노출)을 눈에 박는 장치 */
  .gauge {{ margin-top:14px; }}
  .gauge-row {{ display:flex; align-items:center; gap:10px; margin:5px 0; font-size:.78rem; }}
  .gauge-lab {{ width:74px; color:var(--mist); font-weight:600; flex:none; }}
  .gauge-track {{ flex:1; height:9px; border-radius:5px; background:var(--line); overflow:hidden; }}
  .gauge-fill {{ height:100%; border-radius:5px; }}
  .gauge-val {{ width:96px; text-align:right; font-family:var(--mono);
    font-size:.74rem; color:var(--mist); flex:none; }}
  .gauge-note {{ margin-top:8px; font-size:.76rem; color:var(--mist);
    border-top:1px dashed var(--line-2); padding-top:8px; }}
  .gauge-note b {{ color:var(--alert); }}

  /* ── 계기 카드 ─────────────────────────────── */
  .dial {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:14px 16px; height:100%; }}
  .dial .k {{ font-family:var(--mono); font-size:.66rem; letter-spacing:.1em;
    text-transform:uppercase; color:var(--mist); font-weight:600; }}
  .dial .v {{ font-family:var(--mono); font-size:1.42rem; font-weight:700;
    letter-spacing:-.01em; margin-top:3px; line-height:1.15; }}
  .dial .d {{ font-size:.74rem; color:var(--mist); margin-top:2px; }}

  /* ── 주문 카드 ─────────────────────────────── */
  .order {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:12px 16px; margin-bottom:8px; display:flex; align-items:center; gap:12px; }}
  .order.buy  {{ border-left:4px solid var(--cruise); }}
  .order.sell {{ border-left:4px solid var(--alert); }}
  .tag {{ font-family:var(--mono); font-size:.66rem; font-weight:700; letter-spacing:.08em;
    padding:3px 9px; border-radius:20px; flex:none; }}
  .tag.buy  {{ background:var(--cruise-dim); color:var(--cruise); }}
  .tag.sell {{ background:var(--alert-dim);  color:var(--alert); }}

  /* ── 경고 배너 ─────────────────────────────── */
  .warn {{ border-left:4px solid var(--alert); background:var(--alert-dim);
    border-radius:0 10px 10px 0; padding:14px 18px; margin:14px 0; font-size:.88rem; }}
  .warn h4 {{ margin:0 0 6px; color:var(--alert); font-size:.95rem; }}

  /* Streamlit 기본 metric 정리 */
  div[data-testid="stMetric"] {{ background:var(--panel); border:1px solid var(--line);
    padding:14px; border-radius:12px; }}
  div[data-testid="stMetricValue"] {{ font-family:var(--mono); font-weight:700; }}

  /* 표 */
  .stMarkdown table {{ font-size:.86rem; }}
  .stMarkdown th {{ font-size:.74rem !important; text-transform:uppercase;
    letter-spacing:.05em; color:var(--mist); }}
</style>
""", unsafe_allow_html=True)


def H(html: str) -> str:
    """HTML 문자열의 줄별 들여쓰기를 제거한다.
    Streamlit의 마크다운 파서는 4칸 이상 들여쓴 줄을 '코드블록'으로 해석하므로,
    f-string 안에서 예쁘게 들여쓴 HTML이 그대로 <pre><code>로 렌더링되는 사고가 난다.
    HTML은 줄 앞 공백을 무시하므로 전부 떼어내면 안전하다."""
    return "\n".join(line.strip() for line in html.strip().splitlines())


# ============================================================================
#  전략 파라미터
#  ※ 내부 키는 'Offense'/'Safe' 유지 (기존 CSV·로그·birth_mode 호환).
#    화면 표시는 전부 MODE_META를 통한다.
# ============================================================================
MODE_META = {
    'Offense': {
        'ko': '순항', 'en': 'CRUISE', 'icon': '🛥️', 'color': CRUISE, 'css': 'cruise',
        'why': 'QQQ가 200일선 위 — 추세가 살아있다',
        'job': '작은 티어로 +2.5%씩 꾸준히 수확',
        'expo_label': '저노출', 'expo_mean': 22.0, 'expo_p95': 62.4, 'expo_max_op': 1.17,
    },
    'Safe': {
        'ko': '구조', 'en': 'RESCUE', 'icon': '🚨', 'color': RESCUE, 'css': 'rescue',
        'why': 'QQQ가 200일선 아래 — 추세가 무너졌다',
        'job': '큰 티어를 던져 급락을 낚고 본전에 즉시 탈출',
        'expo_label': '고노출 ⚠️', 'expo_mean': 32.6, 'expo_p95': 87.2, 'expo_max_op': 1.25,
    },
}
def mko(m):   return MODE_META[m]['ko']
def micon(m): return MODE_META[m]['icon']

# 화면 표시용 (퍼센트)
PARAMS = {
    'Safe':    {'buy': -0.1, 'sell': 0.01, 'time': 10},
    'Offense': {'buy': -0.1, 'sell': 2.5,  'time': 8},
}
# 내부 계산용
LOCAL_PARAMS = {
    'Safe':    {'buy': -0.001, 'sell': 1.0001, 'time': 10},
    'Offense': {'buy': -0.001, 'sell': 1.025,  'time': 8},
}
BASE_SPLITS = {'Offense': 6, 'Safe': 4}    # 구조(4분할)가 티어가 더 크다 — 노출 역전의 원인
MAX_SLOTS   = {'Offense': 7, 'Safe': 5}
RESET_CYCLE = 12
UP_RATE     = 0.70
DN_RATE     = 0.50
QREG_SMA_WINDOW = 200

def base_splits_for(mode): return BASE_SPLITS.get(mode, 4)
def max_slots_for(mode):   return MAX_SLOTS.get(mode, 5)


# ============================================================================
#  저장소
# ============================================================================
def _secret(section, key, default=None):
    try:
        return st.secrets[section][key]
    except Exception:
        return default

GH_TOKEN      = _secret("general", "GH_TOKEN")
REPO_KEY      = _secret("general", "REPO_KEY", "yongma11/dongpa6")
HOLDINGS_FILE = "ddulsa200_holdings.csv"
JOURNAL_FILE  = "ddulsa200_journal.csv"
EQUITY_FILE   = "ddulsa200_equity.csv"
SETTINGS_FILE = "ddulsa200_settings.json"
SPREADSHEET_ID        = _secret("general", "SPREADSHEET_ID",
                                "1s8XX-8PUAWyWOHOwst2W-b99pQo1_aFtLVg5uTD_HMI")
WITHDRAWAL_SHEET_NAME = _secret("general", "WITHDRAWAL_SHEET_NAME", "TaxWithdrawals_DDULSA200")

def get_now_kst():
    return datetime.utcnow() + timedelta(hours=9)


# ============================================================================
#  데이터
# ============================================================================
@st.cache_data(ttl=600)
def get_data_final():
    """QQQ / SOXL 조정종가(auto_adjust=True → 분할+배당 반영).
    배당이 가격에 이미 녹아 있으므로 별도 배당 재투자는 하지 않는다(이중계산 방지)."""
    for _ in range(3):
        try:
            end_str = (get_now_kst() + timedelta(days=1)).strftime('%Y-%m-%d')
            dq = yf.download("QQQ",  start='2005-01-01', end=end_str,
                             progress=False, auto_adjust=True, actions=True)
            ds = yf.download("SOXL", start='2005-01-01', end=end_str,
                             progress=False, auto_adjust=True, actions=True)
            if dq.empty or ds.empty:
                time.sleep(1); continue
            qc = dq['Close']['QQQ']  if isinstance(dq.columns, pd.MultiIndex) else dq['Close']
            sc = ds['Close']['SOXL'] if isinstance(ds.columns, pd.MultiIndex) else ds['Close']
            df = pd.DataFrame({'QQQ': qc, 'SOXL': sc})
            df = df.sort_index().dropna(subset=['QQQ', 'SOXL'])
            df['QQQ']  = df['QQQ'].ffill().bfill()
            df['SOXL'] = df['SOXL'].ffill().bfill()
            df.index = df.index.tz_localize(None)
            return df
        except Exception:
            time.sleep(1)
    return None

def calc_mode_series(qqq):
    """QQQ 200일선. 위 → 순항(Offense) / 아래 → 구조(Safe). 밴드 없음(하드 전환).
    ※ 히스테리시스 밴드는 검증 결과 Calmar를 악화시켜 도입하지 않았다(전략 탭 FAQ Q1)."""
    sma = qqq.rolling(QREG_SMA_WINDOW, min_periods=QREG_SMA_WINDOW // 2).mean()
    mode = pd.Series(np.where(qqq.values > sma.values, 'Offense', 'Safe'), index=qqq.index)
    mode[sma.isna().values] = 'Safe'
    dist = (qqq / sma - 1.0) * 100.0
    return mode, dist

def calc_qqq_ma_frame(df):
    price = df['QQQ']
    sma   = price.rolling(QREG_SMA_WINDOW, min_periods=QREG_SMA_WINDOW // 2).mean()
    return pd.DataFrame({'QQQ': price, 'QQQ_SMA200': sma,
                         'QQQ_Dist': (price / sma - 1.0) * 100.0})


# ============================================================================
#  GitHub / 시트
# ============================================================================
def get_repo():
    if not _HAS_GH or GH_TOKEN is None:
        return None
    try:
        return Github(GH_TOKEN).get_repo(REPO_KEY)
    except Exception:
        return None

def load_settings():
    try:
        repo = get_repo()
        if repo:
            return json.loads(repo.get_contents(SETTINGS_FILE).decoded_content.decode("utf-8"))
    except Exception:
        pass
    return {"start_date": "2026-01-23", "init_cap": 100000.0}

def save_settings(d):
    try:
        repo = get_repo(); js = json.dumps(d)
        if repo:
            try:
                c = repo.get_contents(SETTINGS_FILE)
                repo.update_file(c.path, "Update settings", js, c.sha)
            except Exception:
                repo.create_file(SETTINGS_FILE, "Create settings", js)
    except Exception as e:
        print(f"설정 저장 실패: {e}")

def load_csv(fn, cols):
    try:
        repo = get_repo()
        if repo:
            try:
                return pd.read_csv(StringIO(repo.get_contents(fn).decoded_content.decode("utf-8")))
            except Exception:
                pass
    except Exception:
        pass
    return pd.DataFrame(columns=cols)

def save_csv(df, fn):
    try:
        repo = get_repo(); s = df.to_csv(index=False)
        if repo:
            try:
                c = repo.get_contents(fn)
                repo.update_file(c.path, f"Update {fn}", s, c.sha)
            except Exception:
                repo.create_file(fn, f"Create {fn}", s)
    except Exception as e:
        st.error(f"GitHub 저장 실패: {e}")

@st.cache_resource
def get_gspread_workbook():
    if not _HAS_GS:
        return None
    try:
        raw = st.secrets["general"]["GCP_CREDENTIALS"]
    except Exception:
        return None
    try:
        cd = json.loads(raw) if isinstance(raw, str) else dict(raw)
        scopes = ['https://www.googleapis.com/auth/spreadsheets',
                  'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(cd, scopes=scopes)
        return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    except Exception as e:
        print(f"gspread 오픈 실패: {e}")
        return None

def is_seed_memo(memo):
    m = str(memo or "")
    return ("재기" in m) or ("씨앗" in m) or ("seed" in m.lower())

def parse_seed_ref(memo):
    mm = re.search(r"ref\s*=\s*([0-9]+(?:\.[0-9]+)?)", str(memo or ""))
    return float(mm.group(1)) if mm else None

SEED_PCT, SEED_MULT = 0.07, 2.0

def seed_reference(seed_df, anchor):
    if seed_df is None or seed_df.empty:
        return float(anchor)
    s = seed_df.sort_values("날짜")
    ref = parse_seed_ref(s.iloc[-1].get("메모"))
    if ref is not None and ref > 0:
        return ref
    amt = float(s.iloc[-1].get("금액") or 0)
    return amt * (1.0 - SEED_PCT) / SEED_PCT if amt > 0 else float(anchor)

def load_tax_withdrawals():
    cols = ["날짜", "금액", "메모"]
    wb = get_gspread_workbook()
    if wb is None:
        return pd.DataFrame(columns=cols)
    try:
        ws = wb.worksheet(WITHDRAWAL_SHEET_NAME)
        rows = ws.get_all_records()
    except Exception:
        return pd.DataFrame(columns=cols)
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    rn = {}
    for c in df.columns:
        k = str(c).strip()
        if k in ("날짜", "Date", "date"):              rn[c] = "날짜"
        elif k in ("금액", "Amount", "amount", "USD"): rn[c] = "금액"
        elif k in ("메모", "Memo", "memo", "Note"):    rn[c] = "메모"
    df = df.rename(columns=rn)
    for c in cols:
        if c not in df.columns:
            df[c] = "" if c == "메모" else None
    df["날짜"] = pd.to_datetime(df["날짜"], errors='coerce')
    df["금액"] = pd.to_numeric(df["금액"], errors='coerce').fillna(0.0)
    df = df.dropna(subset=["날짜"])
    return df[df["금액"] > 0].sort_values("날짜").reset_index(drop=True)[cols]

def save_tax_withdrawals(df):
    wb = get_gspread_workbook()
    if wb is None:
        st.error("GCP_CREDENTIALS 미설정 — 시트에 저장할 수 없습니다.")
        return False
    try:
        ws = wb.worksheet(WITHDRAWAL_SHEET_NAME)
    except Exception:
        try:
            ws = wb.add_worksheet(title=WITHDRAWAL_SHEET_NAME, rows=200, cols=5)
        except Exception as e:
            st.error(f"시트 탭 생성 실패: {e}")
            return False
    rows = [["날짜", "금액", "메모"]]
    d = df.copy() if df is not None else pd.DataFrame(columns=["날짜", "금액", "메모"])
    if not d.empty and "날짜" in d.columns:
        d = d.dropna(subset=["날짜"]).sort_values("날짜")
        for _, r in d.iterrows():
            try:
                dv = pd.to_datetime(r.get("날짜"))
                amt = float(r.get("금액") or 0)
                if pd.isna(dv) or amt <= 0:
                    continue
                rows.append([dv.strftime("%Y-%m-%d"), round(amt, 2), str(r.get("메모", "") or "")])
            except Exception:
                continue
    try:
        ws.clear(); ws.update(rows); return True
    except Exception as e:
        st.error(f"시트 쓰기 실패: {e}")
        return False


# ============================================================================
#  실전 동기화 엔진
# ============================================================================
def auto_sync_engine(df, start_date, init_cap, withdrawals_df=None):
    empty = (None, None, None, None, None, 1, 0.0, 'Safe', 0.0, 0.0)
    if df is None:
        return empty
    mode_d, dist_d = calc_mode_series(df['QQQ'])
    sim = pd.concat([df['SOXL'], mode_d, dist_d], axis=1)
    sim.columns = ['Price', 'Mode', 'Dist']
    sim = sim.dropna(subset=['Price', 'Mode'])
    end = get_now_kst() - timedelta(days=1)
    sim = sim[(sim.index >= pd.to_datetime(start_date)) & (sim.index <= pd.to_datetime(end.date()))]
    if sim.empty:
        return empty
    sim['PP'] = sim['Price'].shift(1)
    sim['PM'] = sim['Mode'].shift(1)
    sim = sim.dropna(subset=['PP', 'PM'])

    wd = []
    if withdrawals_df is not None and not withdrawals_df.empty:
        wd = [(pd.Timestamp(r["날짜"]).normalize(), float(r["금액"]), str(r.get("메모", "") or ""))
              for _, r in withdrawals_df.iterrows()]
    wi = 0

    cash = op_base = init_cap
    cum_net = last_cycle = cum_wd = 0.0
    slots, journal, daily_eq, log = [], [], [], []
    cyc = 0
    ss = {'Offense': init_cap / base_splits_for('Offense'),
          'Safe':    init_cap / base_splits_for('Safe')}

    for date, row in sim.iterrows():
        price, pp, pm = row['Price'], row['PP'], row['PM']

        # 청산
        sold = []
        for i in range(len(slots) - 1, -1, -1):
            s = slots[i]; s['days'] += 1
            rule = LOCAL_PARAMS[s['birth_mode']]
            hit_tp = price >= s['buy_price'] * rule['sell']
            if hit_tp or s['days'] >= rule['time']:
                rev = s['shares'] * price
                prof = rev - s['shares'] * s['buy_price']
                other = sum(slots[k]['shares'] * price for k in range(len(slots)) if k != i)
                eq_at = cash + rev + other
                journal.append({"날짜": date.date(), "총자산": eq_at, "수익금": prof,
                                "수익률": (prof / (eq_at - prof) * 100) if (eq_at - prof) > 0 else 0})
                log.append({"날짜": date.date(), "구분": "청산",
                            "모드": mko(s['birth_mode']), "가격": f"${price:.2f}",
                            "수량": s['shares'], "손익": f"${prof:,.2f}",
                            "사유": "익절 (LOC)" if hit_tp else "기간만료 (MOC)"})
                cash += rev; cum_net += prof
                sold.append(i)
        for i in sold:
            del slots[i]

        # 비대칭 복리
        cyc += 1
        if cyc >= RESET_CYCLE:
            c = cum_net - last_cycle
            op_base += (UP_RATE * c) if c >= 0 else (DN_RATE * c)
            last_cycle = cum_net
            op_base = max(op_base, 1000.0)
            ss['Offense'] = op_base / base_splits_for('Offense')
            ss['Safe']    = op_base / base_splits_for('Safe')
            cyc = 0

        # 진입 (전일 모드 — 룩어헤드 방지)
        rule = LOCAL_PARAMS[pm]
        loc = pp * (1 + rule['buy'])
        if price <= loc and len(slots) < max_slots_for(pm):
            amt = min(cash, ss[pm])
            sh = int(amt / loc)
            if sh > 0:
                cash -= sh * price
                tr = PARAMS[pm]
                slots.append({'매수일': date.date(), '모드': pm, '매수가': price, '수량': sh,
                              '목표가': price * (1 + tr['sell'] / 100),
                              '손절기한': (date + timedelta(days=int(tr['time'] * 1.45))).date(),
                              'buy_price': price, 'shares': sh, 'days': 0, 'birth_mode': pm})
                log.append({"날짜": date.date(), "구분": "진입", "모드": mko(pm),
                            "가격": f"${price:.2f}", "수량": sh, "손익": "—",
                            "사유": f"LOC ${loc:.2f} 체결"})

        # 인출
        while wi < len(wd) and wd[wi][0] <= pd.Timestamp(date).normalize():
            wa, wm = wd[wi][1], wd[wi][2]
            cash -= wa; cum_wd += wa
            log.append({"날짜": date.date(),
                        "구분": "재기자본 인출" if is_seed_memo(wm) else "양도세 인출",
                        "모드": "—", "가격": "—", "수량": "—",
                        "손익": f"-${wa:,.2f}", "사유": wm or "외부 출금"})
            wi += 1

        daily_eq.append({"날짜": date.date(),
                         "총자산": cash + sum(s['shares'] * price for s in slots)})

    holdings = [{"매수일": s['매수일'], "모드": s['모드'], "매수가": s['매수가'], "수량": s['수량'],
                 "목표가": s['목표가'], "손절기한": s['손절기한']} for s in slots]
    dfl = pd.DataFrame(log)
    if not dfl.empty:
        dfl = dfl.sort_values(by="날짜", ascending=False).reset_index(drop=True)
    tmode = mode_d.iloc[-1] if not mode_d.empty else 'Safe'
    tdist = float(dist_d.dropna().iloc[-1]) if not dist_d.dropna().empty else 0.0
    # 현재 노출률 (실전 계기판용)
    last_px = float(df['SOXL'].iloc[-1])
    inv = sum(s['shares'] * last_px for s in slots)
    tot = cash + inv
    expo = inv / tot if tot > 0 else 0.0
    return (pd.DataFrame(holdings), pd.DataFrame(journal), pd.DataFrame(daily_eq), dfl,
            ss.get(tmode, ss['Safe']), (cyc % RESET_CYCLE) + 1, cum_wd, tmode, tdist, expo)


# ============================================================================
#  백테스트 엔진
# ============================================================================
def run_backtest(df, start_date, end_date, init_cap,
                 include_fees=False, include_tax=False,
                 buy_fee=0.00015, sell_fee=0.0001706,
                 ded_usd=1786.0, tax_rate=0.22,
                 up_rate=None, dn_rate=None):
    if up_rate is None: up_rate = UP_RATE
    if dn_rate is None: dn_rate = DN_RATE
    if df is None:
        return None, None, None, None

    mode_d, dist_d = calc_mode_series(df['QQQ'])
    qm = calc_qqq_ma_frame(df)
    sim = pd.concat([df['SOXL'], mode_d, dist_d, qm['QQQ'], qm['QQQ_SMA200'], qm['QQQ_Dist']], axis=1)
    sim.columns = ['Price', 'Mode', 'Dist', 'QQQ', 'SMA200', 'QDist']
    sim = sim.dropna(subset=['Price', 'Mode'])
    sim = sim[(sim.index >= pd.to_datetime(start_date)) & (sim.index <= pd.to_datetime(end_date))]
    if sim.empty:
        return None, None, None, None
    sim['PP'] = sim['Price'].shift(1)
    sim['PM'] = sim['Mode'].shift(1)
    sim = sim.dropna(subset=['PP', 'PM'])

    cash = op_base = init_cap
    cum_net = last_cycle = 0.0
    slots, curve, logs = [], [], []
    gp = gl = 0.0
    cyc = 0
    ss = {'Offense': init_cap / base_splits_for('Offense'),
          'Safe':    init_cap / base_splits_for('Safe')}
    tot_bf = tot_sf = 0.0
    annual_realized = total_tax = 0.0
    last_year = None
    yr_real, yr_fee, yr_tax = {}, {}, {}
    pending = []
    n_tp = n_time = 0

    for date, row in sim.iterrows():
        price, pp, pm = row['Price'], row['PP'], row['PM']
        yr = date.year

        # 연도 전환 → 양도세 예약
        if last_year is not None and yr != last_year:
            yr_real[last_year] = annual_realized
            if include_tax:
                tax = max(0.0, annual_realized - ded_usd) * tax_rate
                if tax > 0:
                    pending.append({'amt': tax, 'earliest': pd.Timestamp(year=yr, month=5, day=1),
                                    'force': pd.Timestamp(year=yr, month=5, day=31),
                                    'paid': 0.0})
            annual_realized = 0.0
        last_year = yr

        # 청산
        sold, sq, sp = [], 0, 0.0
        for i in range(len(slots) - 1, -1, -1):
            s = slots[i]; s['days'] += 1
            rule = LOCAL_PARAMS[s['birth_mode']]
            hit_tp = price >= s['buy_price'] * rule['sell']
            if hit_tp or s['days'] >= rule['time']:
                gross = s['shares'] * price
                fee = gross * sell_fee if include_fees else 0.0
                net = gross - fee
                prof = net - s.get('cost_basis', s['shares'] * s['buy_price'])
                cash += net; cum_net += prof
                tot_sf += fee
                yr_fee[yr] = yr_fee.get(yr, 0.0) + fee
                annual_realized += prof
                if prof > 0: gp += prof
                else:        gl += abs(prof)
                if hit_tp: n_tp += 1
                else:      n_time += 1
                sold.append(i); sq += s['shares']; sp += prof
        for i in sold:
            del slots[i]
        if sq > 0:
            alloc = sum(s['shares'] * price for s in slots)
            ta = cash + alloc
            logs.append({"날짜": date.date(), "구분": "청산", "모드": mko(pm),
                         "종가": f"${price:.2f}", "수량": f"{-sq:+,d}",
                         "실현손익": f"${sp:,.2f}",
                         "보유수량": f"{sum(s['shares'] for s in slots):,d}",
                         "현금": f"${cash:,.0f}", "평가액": f"${alloc:,.0f}",
                         "총자산": f"${ta:,.0f}",
                         "노출%": f"{alloc/ta*100:.0f}%" if ta > 0 else "0%",
                         "운용원금": f"${op_base:,.0f}",
                         "QQQ이격%": f"{row['QDist']:+.1f}" if pd.notna(row['QDist']) else ""})

        # 비대칭 복리
        cyc += 1
        if cyc >= RESET_CYCLE:
            c = cum_net - last_cycle
            op_base += (up_rate * c) if c >= 0 else (dn_rate * c)
            last_cycle = cum_net
            op_base = max(op_base, 1000.0)
            ss['Offense'] = op_base / base_splits_for('Offense')
            ss['Safe']    = op_base / base_splits_for('Safe')
            cyc = 0

        # 진입
        rule = LOCAL_PARAMS[pm]
        loc = pp * (1 + rule['buy'])
        if price <= loc and len(slots) < max_slots_for(pm):
            amt = min(cash, ss[pm])
            sh = int(amt / loc)
            if sh > 0:
                inv = sh * price
                fee = inv * buy_fee if include_fees else 0.0
                cost = inv + fee
                cash -= cost
                tot_bf += fee
                yr_fee[yr] = yr_fee.get(yr, 0.0) + fee
                slots.append({'buy_price': price, 'shares': sh, 'days': 0,
                              'birth_mode': pm, 'cost_basis': cost})
                alloc = sum(s['shares'] * price for s in slots)
                ta = cash + alloc
                logs.append({"날짜": date.date(), "구분": "진입", "모드": mko(pm),
                             "종가": f"${price:.2f}", "수량": f"+{sh:,d}", "실현손익": "—",
                             "보유수량": f"{sum(s['shares'] for s in slots):,d}",
                             "현금": f"${cash:,.0f}", "평가액": f"${alloc:,.0f}",
                             "총자산": f"${ta:,.0f}",
                             "노출%": f"{alloc/ta*100:.0f}%" if ta > 0 else "0%",
                             "운용원금": f"${op_base:,.0f}",
                             "QQQ이격%": f"{row['QDist']:+.1f}" if pd.notna(row['QDist']) else ""})

        alloc = sum(s['shares'] * price for s in slots)
        curve.append({'Date': date, 'Equity': cash + alloc,
                      'Expo': alloc / (cash + alloc) if (cash + alloc) > 0 else 0,
                      'Mode': row['Mode']})

        # 양도세 인출
        for t in pending:
            rem = t['amt'] - t['paid']
            if rem <= 1e-6: continue
            if date >= t['force']:
                cash -= rem; total_tax += rem; t['paid'] += rem
                yr_tax[yr] = yr_tax.get(yr, 0.0) + rem
            elif date >= t['earliest'] and cyc == 0:
                act = min(rem, max(0.0, cash))
                if act > 0:
                    cash -= act; total_tax += act; t['paid'] += act
                    yr_tax[yr] = yr_tax.get(yr, 0.0) + act
        pending = [t for t in pending if (t['amt'] - t['paid']) > 1e-6]

    if last_year is not None:
        yr_real.setdefault(last_year, annual_realized)
    pend_tax = (max(0.0, annual_realized - ded_usd) * tax_rate if include_tax else 0.0) \
               + sum(t['amt'] - t['paid'] for t in pending)

    res = pd.DataFrame(curve).set_index('Date')
    dfl = pd.DataFrame(logs).reset_index(drop=True) if logs else pd.DataFrame()

    if not res.empty:
        r = res['Equity'].pct_change()
        dn = r[r < 0].std() * np.sqrt(252)
        vol = r.std() * np.sqrt(252)
        tot_ret = res['Equity'].iloc[-1] / init_cap - 1
        days = (res.index[-1] - res.index[0]).days
        cagr = (1 + tot_ret) ** (365 / days) - 1 if days > 0 else 0
        metrics = {'pf': gp / gl if gl > 0 else 99.9,
                   'sortino': cagr / dn if dn > 0 else 0,
                   'sharpe': (r.mean() * 252) / vol if vol > 0 else 0,
                   'fees': tot_bf + tot_sf, 'tax': total_tax, 'pend_tax': pend_tax,
                   'include_fees': include_fees, 'include_tax': include_tax,
                   'expo': res['Expo'].mean(), 'n_tp': n_tp, 'n_time': n_time}
    else:
        metrics = {'pf': 0, 'sortino': 0, 'sharpe': 0, 'fees': 0, 'tax': 0, 'pend_tax': 0,
                   'include_fees': include_fees, 'include_tax': include_tax,
                   'expo': 0, 'n_tp': 0, 'n_time': 0}

    def mdd(s):
        return ((s - s.cummax()) / s.cummax()).min()

    ys = []
    prev = init_cap
    for y in res.index.year.unique():
        d = res[res.index.year == y]
        end_eq = d['Equity'].iloc[-1]
        ys.append({"연도": y, "수익률": (end_eq - prev) / prev, "MDD": mdd(d['Equity']),
                   "기말자산": end_eq, "수수료": yr_fee.get(y, 0.0), "양도세": yr_tax.get(y, 0.0),
                   "실현손익": yr_real.get(y, 0.0)})
        prev = end_eq
    return res, metrics, pd.DataFrame(ys).set_index("연도"), dfl


# ============================================================================
#  차트 (Plotly)
#    · 한글: 브라우저가 렌더링 → matplotlib 한글 폰트 깨짐 없음
#    · 호버: 마우스를 올리면 날짜별 수치 표시
#    · 벤치마크: SOXL 매수후보유를 함께 그린다
# ============================================================================
BH_COLOR = "#98A2B0"

def _layout(fig, height=380, legend=True):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Pretendard, system-ui, sans-serif", size=12, color=MIST),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="rgba(255,255,255,.96)", bordercolor="#D9DEE4",
                        font=dict(family="Pretendard, sans-serif", size=12, color="#0F1B26")),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=-0.22,
                    xanchor="center", x=0.5, font=dict(size=11)),
        dragmode="pan")
    fig.update_xaxes(showgrid=False, showline=True, linewidth=1, linecolor="#D9DEE4",
                     zeroline=False, ticks="outside", tickcolor="#D9DEE4")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(124,143,163,.16)",
                     zeroline=False, showline=False)
    return fig


def equity_chart(res, bh):
    """자산 곡선 — 전략 vs SOXL 매수후보유 (로그축)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bh.index, y=bh.values, name="SOXL 매수후보유",
        line=dict(color=BH_COLOR, width=1.4),
        hovertemplate="SOXL B&H  <b>$%{y:,.0f}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=res.index, y=res['Equity'], name="떨사 200",
        line=dict(color=CRUISE, width=2.1),
        hovertemplate="떨사 200  <b>$%{y:,.0f}</b><extra></extra>"))
    fig.update_yaxes(type="log", tickprefix="$", tickformat=",.0f")
    return _layout(fig, 400)


def dd_chart(res, bh):
    """낙폭 — 전략 vs SOXL B&H. 몬테카를로 기대 MDD(−47%) 기준선."""
    bdd = (bh - bh.cummax()) / bh.cummax() * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bdd.index, y=bdd.values, name="SOXL B&H 낙폭",
        line=dict(color=BH_COLOR, width=1.2),
        hovertemplate="SOXL B&H  <b>%{y:.1f}%</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=res.index, y=res['DD'] * 100, name="떨사 200 낙폭",
        line=dict(color=ALERT, width=1.4), fill="tozeroy",
        fillcolor="rgba(209,69,59,.16)",
        hovertemplate="떨사 200  <b>%{y:.1f}%</b><extra></extra>"))
    fig.add_hline(y=-47, line=dict(color=MIST, width=1, dash="dash"),
                  annotation_text="몬테카를로 기대 MDD −47%",
                  annotation_position="bottom left",
                  annotation_font=dict(size=10, color=MIST))
    fig.update_yaxes(ticksuffix="%")
    return _layout(fig, 250)


def yearly_chart(yr):
    """연도별 수익률 막대 + 연중 최대낙폭 선."""
    years = [str(int(y)) for y in yr.index]
    rets = (yr['수익률'] * 100).values
    mdds = (yr['MDD'] * 100).values
    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=rets, name="연간 수익률",
        marker_color=[CRUISE if r >= 0 else ALERT for r in rets], marker_line_width=0,
        text=[f"{r:+.0f}%" for r in rets], textposition="outside",
        textfont=dict(size=10, color=MIST),
        hovertemplate="수익률  <b>%{y:+.1f}%</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=years, y=mdds, name="연중 최대낙폭", mode="lines+markers",
        line=dict(color=RESCUE, width=1.8), marker=dict(size=6),
        hovertemplate="연중 MDD  <b>%{y:.1f}%</b><extra></extra>"))
    fig.add_hline(y=0, line=dict(color=MIST, width=1))
    fig.update_yaxes(ticksuffix="%")
    fig.update_layout(bargap=.35)
    return _layout(fig, 360)


def live_equity_chart(de, ic):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=de['날짜'], y=de['총자산'], name="총자산",
        line=dict(color=CRUISE, width=2.1), fill="tozeroy",
        fillcolor="rgba(14,147,132,.08)",
        hovertemplate="총자산  <b>$%{y:,.0f}</b><extra></extra>"))
    fig.add_hline(y=ic, line=dict(color=MIST, width=1, dash="dash"),
                  annotation_text="시작 원금", annotation_position="top left",
                  annotation_font=dict(size=10, color=MIST))
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return _layout(fig, 320, legend=False)


# ============================================================================
#  UI 조각
# ============================================================================
def bridge_banner(mode, dist, expo=None):
    """SIGNATURE — 모드 배너 + 노출 게이지.
    순항=저노출 / 구조=고노출 이라는 반직관을 화면에서 직접 보여준다."""
    m = MODE_META[mode]
    other = 'Safe' if mode == 'Offense' else 'Offense'
    o = MODE_META[other]
    p = PARAMS[mode]

    def bar(meta, active):
        pct = min(meta['expo_p95'], 100)
        col = meta['color'] if active else "var(--line-2)"
        lab = meta['color'] if active else "var(--mist)"
        op  = "1" if active else ".35"
        return (f'<div class="gauge-row">'
                f'<span class="gauge-lab" style="color:{lab}">{meta["icon"]} {meta["ko"]}</span>'
                f'<span class="gauge-track">'
                f'<span class="gauge-fill" style="width:{pct}%;background:{col};opacity:{op}"></span>'
                f'</span>'
                f'<span class="gauge-val">평균 {meta["expo_mean"]:.0f}% · p95 {meta["expo_p95"]:.0f}%</span>'
                f'</div>')

    st.markdown(H(f"""
    <div class="bridge {m['css']}">
      <div class="bridge-top">
        <span class="bridge-name">{m['icon']} {m['ko']} 모드</span>
        <span class="bridge-en">{m['en']}</span>
        <span class="num" style="color:var(--mist);font-size:.82rem;">
          QQQ 200일선 이격 {dist:+.2f}%</span>
      </div>
      <div class="bridge-why">{m['why']} → <b>{m['job']}</b></div>
      <div class="bridge-rules">
        <b>{base_splits_for(mode)}분할</b> (최대 {max_slots_for(mode)}티어) ·
        진입 <b>전일 −0.1%</b> · 익절 <b>+{p['sell']}%</b> · 시간손절 <b>{p['time']}일</b>
      </div>
      <div class="gauge">
        {bar(MODE_META['Offense'], mode == 'Offense')}
        {bar(MODE_META['Safe'],    mode == 'Safe')}
        <div class="gauge-note">
          ⚠️ 이름의 함정: <b>구조 모드가 순항보다 노출이 높다.</b>
          티어 크기 = 운용원금 ÷ 분할수 이므로 4분할(구조) 티어가 6분할(순항)보다 크다.
          최대 노출 구조 <b>1.25×</b> vs 순항 1.17× — 구조는 자본보존이 아니라
          <b>급반등 스캘핑</b> 모드다.
        </div>
      </div>
    </div>
    """), unsafe_allow_html=True)


def dial(k, v, d=""):
    return H(f"""<div class="dial">
    <div class="k">{k}</div>
    <div class="v">{v}</div>
    <div class="d">{d}</div>
    </div>""")


# ============================================================================
#  MAIN
# ============================================================================
def main():
    st.markdown(H("""
    <div class="mast">
      <div class="eyebrow">Ddulsa 200 · Navigation Console · v2.0</div>
      <h1>떨사 200</h1>
      <div class="sub">QQQ 200일선으로 계절을 읽고 · SOXL 급락을 분할로 담는다 —
        <span class="num">순항(Cruise)</span> / <span class="num">구조(Rescue)</span></div>
    </div>
    """), unsafe_allow_html=True)

    tab_t, tab_b, tab_l = st.tabs(["🛥️  실전 트레이딩", "🧪  백테스트", "📚  전략 로직"])

    with st.spinner("시세 수신 중…"):
        df = get_data_final()
    offline = df is None
    if offline:
        st.warning("오프라인 — 시세를 받지 못했습니다. 잠시 후 새로고침하세요.")

    if not offline:
        mode_s, dist_s = calc_mode_series(df['QQQ'])
        curr_mode = mode_s.iloc[-1]
        curr_dist = float(dist_s.dropna().iloc[-1]) if not dist_s.dropna().empty else 0.0
        px, prev_px = df['SOXL'].iloc[-1], df['SOXL'].iloc[-2]
    else:
        curr_mode, curr_dist, px, prev_px = 'Safe', 0.0, 0.0, 0.0

    settings = load_settings()
    if 'auto_run_done' not in st.session_state:
        st.session_state['auto_run_done'] = False
    try:
        sd = datetime.strptime(settings.get("start_date", "2026-01-23"), "%Y-%m-%d").date()
        ic = float(settings.get("init_cap", 100000.0))
    except Exception:
        sd, ic = datetime(2026, 1, 23).date(), 100000.0

    wdf = load_tax_withdrawals()
    if not offline and ('holdings' not in st.session_state or not st.session_state['auto_run_done']):
        (h, j, eq, lg, slot, cyc, cwd, tmode, tdist, texpo) = auto_sync_engine(df, sd, ic, wdf)
        if h is not None:
            old = load_csv(HOLDINGS_FILE, h.columns)
            if len(h) != len(old) or (not old.empty and str(h.iloc[-1].values) != str(old.iloc[-1].values)):
                save_csv(h, HOLDINGS_FILE); save_csv(j, JOURNAL_FILE); save_csv(eq, EQUITY_FILE)
            st.session_state.update({'holdings': h, 'journal': j, 'equity_history': eq,
                                     'action_log': lg, 'slot_size': slot, 'cycle': cyc,
                                     'cum_wd': cwd, 'expo': texpo, 'auto_run_done': True})

    for k, cols in [('holdings', ["매수일","모드","매수가","수량","목표가","손절기한"]),
                    ('journal',  ["날짜","총자산","수익금","수익률"]),
                    ('equity_history', ["날짜","총자산"])]:
        if k not in st.session_state:
            st.session_state[k] = load_csv({'holdings': HOLDINGS_FILE, 'journal': JOURNAL_FILE,
                                            'equity_history': EQUITY_FILE}[k], cols)
    if 'action_log' not in st.session_state:
        st.session_state['action_log'] = pd.DataFrame()

    # ---------------------------------------------------------- 실전
    with tab_t:
        _sidebar(sd, ic, offline)
        slot_sz = st.session_state.get('slot_size', ic / base_splits_for(curr_mode))
        cyc     = st.session_state.get('cycle', 1)
        expo    = st.session_state.get('expo', 0.0)
        today   = get_now_kst().date()

        if not offline:
            bridge_banner(curr_mode, curr_dist, expo)

        chg = ((px - prev_px) / prev_px * 100) if (not offline and prev_px > 0) else 0
        c = st.columns(5)
        c[0].markdown(dial("SOXL 종가", f"${px:.2f}" if not offline else "—",
                           f"{chg:+.2f}% (전일 대비)" if not offline else "오프라인"),
                      unsafe_allow_html=True)
        c[1].markdown(dial("1티어 예산", f"${slot_sz:,.0f}",
                           f"운용원금 ÷ {base_splits_for(curr_mode)}"), unsafe_allow_html=True)
        c[2].markdown(dial("현재 노출", f"{expo*100:.0f}%",
                           f"보유 {len(st.session_state['holdings'])} / 최대 {max_slots_for(curr_mode)}티어"),
                      unsafe_allow_html=True)
        c[3].markdown(dial("복리 사이클", f"{cyc}일차", f"{RESET_CYCLE}일마다 재산정"),
                      unsafe_allow_html=True)
        c[4].markdown(dial("누적 인출", f"${st.session_state.get('cum_wd', 0):,.0f}",
                           "양도세 + 재기자본"), unsafe_allow_html=True)

        st.markdown("### 오늘의 주문")
        if offline:
            st.info("시세를 받으면 주문표가 표시됩니다.")
        else:
            dh = st.session_state['holdings']
            sells, buys = [], []
            if not dh.empty:
                dh['손절기한'] = pd.to_datetime(dh['손절기한']).dt.date
                for i, r in dh.iterrows():
                    if r['손절기한'] <= today:
                        sells.append(f"티어 {i+1} — <b class='num'>{r['수량']}주</b> 시장가 청산 "
                                     f"<span style='color:var(--mist)'>기간만료 · MOC</span>")
                    else:
                        sells.append(f"티어 {i+1} — <b class='num'>{r['수량']}주</b> "
                                     f"<b class='num'>${r['목표가']:.2f}</b> 지정가 "
                                     f"<span style='color:var(--mist)'>익절 대기 · LOC</span>")
            r = PARAMS[curr_mode]
            lim = px * (1 + r['buy'] / 100)
            qty = int(slot_sz / lim)
            buys.append(f"신규 티어 — <b class='num'>{qty}주</b> "
                        f"<b class='num'>${lim:.2f}</b> 이하 "
                        f"<span style='color:var(--mist)'>{mko(curr_mode)} 진입 · LOC</span>")
            for o in sells:
                st.markdown(H(f'<div class="order sell"><span class="tag sell">청산</span>'
                              f'<span>{o}</span></div>'), unsafe_allow_html=True)
            for o in buys:
                st.markdown(H(f'<div class="order buy"><span class="tag buy">진입</span>'
                              f'<span>{o}</span></div>'), unsafe_allow_html=True)

        st.divider()
        _tax_section(wdf, today)
        _seed_section(wdf, ic, today)

        st.divider()
        st.markdown("### 보유 티어")
        dh = st.session_state['holdings']
        if not dh.empty:
            v = dh.copy()
            v['매수일'] = pd.to_datetime(v['매수일']).dt.date
            v['모드'] = v['모드'].map(lambda m: f"{micon(m)} {mko(m)}")
            v.index = range(1, len(v) + 1); v.index.name = "티어"
            if not offline:
                y = (px - dh['매수가']) / dh['매수가'] * 100
                v['평가손익'] = [f"{'▲' if x > 0 else '▼'} {abs(x):.2f}%" for x in y]
                v['상태'] = ["MOC 청산 예정" if r['손절기한'] <= today else "LOC 대기"
                            for _, r in dh.iterrows()]
                tq = dh['수량'].sum(); ti = (dh['매수가'] * dh['수량']).sum()
                tp = tq * px - ti
                m = st.columns(4)
                m[0].markdown(dial("보유 수량", f"{tq:,}주"), unsafe_allow_html=True)
                m[1].markdown(dial("평균 단가", f"${ti/tq:,.2f}" if tq else "—"), unsafe_allow_html=True)
                m[2].markdown(dial("평가 손익", f"${tp:,.0f}"), unsafe_allow_html=True)
                m[3].markdown(dial("평가 수익률", f"{tp/ti*100:+.2f}%" if ti else "—"),
                              unsafe_allow_html=True)
                st.write("")
            st.dataframe(v, use_container_width=True)
        else:
            st.info("보유 중인 티어가 없습니다. 조건 충족 시 진입합니다.")

        st.divider()
        st.markdown("### 자산 곡선")
        de = st.session_state['equity_history']
        dj = st.session_state['journal']
        if not dj.empty:
            tp = dj['수익금'].sum()
            m = st.columns(3)
            m[0].markdown(dial("시작 원금", f"${ic:,.0f}"), unsafe_allow_html=True)
            m[1].markdown(dial("누적 실현손익", f"${tp:,.0f}"), unsafe_allow_html=True)
            m[2].markdown(dial("총 수익률", f"{tp/ic*100:+.1f}%"), unsafe_allow_html=True)
            st.write("")
        if not de.empty:
            de['날짜'] = pd.to_datetime(de['날짜']); de = de.sort_values("날짜")
            st.plotly_chart(live_equity_chart(de, ic), use_container_width=True,
                            config={"displayModeBar": False})

        with st.expander(f"매매 기록 (전략 시작 {sd} 이후)"):
            dl = st.session_state['action_log']
            if not dl.empty:
                st.dataframe(dl, use_container_width=True, hide_index=True)
            else:
                st.caption("아직 기록된 매매가 없습니다.")

    # ---------------------------------------------------------- 백테스트
    with tab_b:
        _backtest_tab(df, offline)

    # ---------------------------------------------------------- 전략
    with tab_l:
        _strategy_tab()


# ============================================================================
def _sidebar(sd, ic, offline):
    with st.sidebar:
        st.markdown("### 설정")
        nd = st.date_input("전략 시작일", value=sd)
        nc = st.number_input("시작 원금 ($)", value=ic, step=100.0)
        if not offline:
            if st.button("설정 저장 · 재동기화", type="primary", use_container_width=True):
                save_settings({"start_date": nd.strftime("%Y-%m-%d"), "init_cap": nc})
                st.session_state['auto_run_done'] = False
                st.rerun()

        st.divider()
        st.markdown("### 모드 규칙")
        for k in ['Offense', 'Safe']:
            m = MODE_META[k]; p = PARAMS[k]
            st.markdown(H(f"""
            <div style="border-left:3px solid {m['color']};padding:8px 0 8px 12px;margin-bottom:10px;">
              <div style="font-weight:700;color:{m['color']};">{m['icon']} {m['ko']} · {m['expo_label']}</div>
              <div style="font-size:.78rem;color:var(--mist);margin-top:2px;">{m['why']}</div>
              <div class="num" style="font-size:.76rem;margin-top:4px;">
                {base_splits_for(k)}분할 · 익절 +{p['sell']}% · {p['time']}일</div>
            </div>"""), unsafe_allow_html=True)

        st.caption(f"비대칭 복리 · {RESET_CYCLE}일 주기 · 이익 {UP_RATE:.0%} / 손실 {DN_RATE:.0%} 반영")

        st.divider()
        st.markdown(H(f"""
        <div class="warn">
          <h4>기대 낙폭 −47%</h4>
          몬테카를로 기준 <b>기대 MDD는 −47%</b>입니다.
          실현 −29%는 상위 5% 행운이었습니다.<br>
          <span class="num">P(&lt;−50%) = 41% · P(&lt;−60%) = 20%</span><br>
          자금관리는 <b>−45~50%</b>를 전제로.
        </div>"""), unsafe_allow_html=True)

        st.divider()
        if st.button("데이터 초기화", use_container_width=True):
            save_csv(pd.DataFrame(columns=["매수일","모드","매수가","수량","목표가","손절기한"]), HOLDINGS_FILE)
            save_csv(pd.DataFrame(columns=["날짜","총자산","수익금","수익률"]), JOURNAL_FILE)
            save_csv(pd.DataFrame(columns=["날짜","총자산"]), EQUITY_FILE)
            for k in ['holdings','journal','equity_history','action_log']:
                st.session_state.pop(k, None)
            st.rerun()


# ============================================================================
def _backtest_tab(df, offline):
    st.markdown("### 백테스트")
    if offline:
        st.info("시세를 받으면 백테스트를 실행할 수 있습니다.")
        return
    today = get_now_kst().date()
    c = st.columns([1, 1, 1])
    cap = c[0].number_input("초기 자본 ($)", value=10000.0, step=1000.0)
    s_d = c[1].date_input("시작", value=datetime(2010, 1, 1), min_value=datetime(2000, 1, 1))
    e_d = c[2].date_input("종료", value=today, min_value=datetime(2000, 1, 1))

    with st.expander("비용 · 세금 반영"):
        o = st.columns(2)
        fees = o[0].checkbox("거래 수수료", value=False, help="매수 0.015% / 매도 0.01706%")
        tax  = o[1].checkbox("양도세 (5월 인출)", value=False)
        if tax:
            fx  = st.number_input("USD/KRW", value=1400, min_value=800, max_value=2000, step=10)
            ded = st.number_input("연 공제 (KRW)", value=2_500_000, step=100_000) / fx
            st.caption(f"공제 ${ded:,.0f} · 세율 22%")
        else:
            ded = 1786.0

    with st.expander("비대칭 복리 다이얼 (수익 ↔ 낙폭)"):
        cc = st.columns(2)
        up = cc[0].slider("이익 재투자율", 0.50, 1.00, float(UP_RATE), 0.05)
        dn = cc[1].slider("손실 반영률",   0.10, 1.00, float(DN_RATE), 0.05)
        st.caption("검증 결과: 이 다이얼은 위험–수익 직선 위를 움직일 뿐 알파를 더하지 않습니다. "
                   "기본값 0.70 / 0.50 이 Calmar 최적(1.35).")

    if st.button("백테스트 실행", type="primary"):
        with st.spinner("계산 중…"):
            res, mt, yr, lg = run_backtest(df, s_d, e_d, cap, include_fees=fees,
                                           include_tax=tax, ded_usd=ded,
                                           up_rate=up, dn_rate=dn)
        if res is None:
            st.error("해당 기간에 데이터가 부족합니다.")
            return

        fin = res['Equity'].iloc[-1]
        ret = fin / cap - 1
        days = (res.index[-1] - res.index[0]).days
        cagr = (1 + ret) ** (365 / days) - 1 if days > 0 else 0
        res['DD'] = (res['Equity'] - res['Equity'].cummax()) / res['Equity'].cummax()
        mdd = res['DD'].min()
        calmar = cagr / abs(mdd) if mdd else 0

        m = st.columns(6)
        m[0].markdown(dial("최종 자산", f"${fin:,.0f}", f"{ret*100:+,.0f}%"), unsafe_allow_html=True)
        m[1].markdown(dial("CAGR", f"{cagr*100:.1f}%"), unsafe_allow_html=True)
        m[2].markdown(dial("MDD", f"{mdd*100:.1f}%", "실현 경로"), unsafe_allow_html=True)
        m[3].markdown(dial("Calmar", f"{calmar:.2f}"), unsafe_allow_html=True)
        m[4].markdown(dial("Sharpe", f"{mt['sharpe']:.2f}"), unsafe_allow_html=True)
        m[5].markdown(dial("평균 노출", f"{mt['expo']*100:.0f}%", "나머지는 현금"),
                      unsafe_allow_html=True)

        st.markdown(H(f"""
        <div class="warn">
          <h4>이 MDD는 단일 경로의 값입니다</h4>
          블록 부트스트랩 <span class="num">1,000</span>회 기준 <b>기대 MDD는 −47%</b>이며,
          실현 −29%는 상위 5% 행운이었습니다.
          <span class="num">P(&lt;−40%)=72% · P(&lt;−50%)=41% · P(&lt;−60%)=20%</span>.
          자세한 내용은 <b>전략 로직 → 몬테카를로</b>를 보세요.
        </div>"""), unsafe_allow_html=True)

        if mt['include_fees'] or mt['include_tax']:
            t = st.columns(4)
            t[0].markdown(dial("누적 수수료", f"${mt['fees']:,.0f}"), unsafe_allow_html=True)
            t[1].markdown(dial("납부 양도세", f"${mt['tax']:,.0f}"), unsafe_allow_html=True)
            t[2].markdown(dial("미정산 세금", f"${mt['pend_tax']:,.0f}"), unsafe_allow_html=True)
            aft = (fin - mt['pend_tax']) / cap
            ac = aft ** (365 / days) - 1 if days > 0 else 0
            t[3].markdown(dial("세후 CAGR", f"{ac*100:.1f}%", f"{(ac-cagr)*100:+.1f}pp"),
                          unsafe_allow_html=True)

        # ── SOXL 매수후보유 벤치마크 ──
        bpx = df['SOXL'].reindex(res.index).ffill()
        bh = cap * bpx / bpx.iloc[0]
        b_ret = bh.iloc[-1] / cap - 1
        b_cagr = (1 + b_ret) ** (365 / days) - 1 if days > 0 else 0
        b_mdd = ((bh - bh.cummax()) / bh.cummax()).min()
        b_cal = b_cagr / abs(b_mdd) if b_mdd else 0

        st.markdown("#### 자산 곡선 · SOXL 매수후보유 대비")
        b = st.columns(4)
        b[0].markdown(dial("떨사 200 최종", f"${fin:,.0f}", f"CAGR {cagr*100:.1f}%"),
                      unsafe_allow_html=True)
        b[1].markdown(dial("SOXL B&H 최종", f"${bh.iloc[-1]:,.0f}", f"CAGR {b_cagr*100:.1f}%"),
                      unsafe_allow_html=True)
        b[2].markdown(dial("낙폭 비교", f"{mdd*100:.1f}%", f"B&H {b_mdd*100:.1f}%"),
                      unsafe_allow_html=True)
        b[3].markdown(dial("Calmar 비교", f"{calmar:.2f}", f"B&H {b_cal:.2f}"),
                      unsafe_allow_html=True)
        st.write("")
        st.plotly_chart(equity_chart(res, bh), use_container_width=True,
                        config={"displayModeBar": False})

        st.markdown("#### 낙폭")
        st.plotly_chart(dd_chart(res, bh), use_container_width=True,
                        config={"displayModeBar": False})
        st.caption("마우스를 올리면 날짜별 수치가 표시됩니다.")

        st.markdown("#### 연도별 성과")
        st.plotly_chart(yearly_chart(yr), use_container_width=True,
                        config={"displayModeBar": False})
        y = yr.copy()
        y['수익률'] = y['수익률'].apply(lambda x: f"{x*100:+.1f}%")
        y['MDD']   = y['MDD'].apply(lambda x: f"{x*100:.1f}%")
        y['기말자산'] = y['기말자산'].apply(lambda x: f"${x:,.0f}")
        for col, on in [('수수료', mt['include_fees']), ('양도세', mt['include_tax']),
                        ('실현손익', mt['include_tax'])]:
            if on: y[col] = y[col].apply(lambda x: f"${x:,.0f}")
            else:  y = y.drop(columns=[col], errors='ignore')
        st.dataframe(y.T, use_container_width=True)

        if not lg.empty:
            st.markdown("#### 매매 로그")
            st.caption(f"총 {len(lg):,}건 · 익절 {mt['n_tp']:,} / 기간만료 {mt['n_time']:,} "
                       f"({mt['n_tp']/(mt['n_tp']+mt['n_time'])*100:.0f}%가 익절)")
            st.dataframe(lg.sort_values('날짜', ascending=False).reset_index(drop=True),
                         use_container_width=True, height=420, hide_index=True)


# ============================================================================
#  전략 로직 탭
# ============================================================================
def _strategy_tab():
    st.markdown("### 떨사 200 전략 매뉴얼")
    st.caption("v2.0 · 검증 기간 2010-03 ~ 2026-07 (16.3년, SOXL)")

    # ── 1. 정체 ──────────────────────────────────
    st.markdown("#### 1 · 이 전략의 정체")
    st.info("""
**한 줄 정의 — 방향성 베팅이 아니라, 레버리지 ETF의 변동성을 수확하는 시스템입니다.**

레버리지 ETF의 최대 약점은 변동성 감쇠(volatility decay)입니다. 등락이 반복되면 지수는 제자리여도
3배 ETF는 잠식되죠. 매수후보유는 이 감쇠를 온몸으로 맞지만, **이 전략은 그 등락을 오히려 수확합니다.**
급락에 사서 반등에 파는 회전이 변동성을 손실이 아니라 수익원으로 바꿉니다.

그래서 **SOXL이 폭락한 2022년에도 전략은 +44.5%** 를 냈습니다. 버그가 아니라 설계의 본질이 드러난 지점입니다.
평균 노출이 24%에 불과해 대부분 현금을 들고 있다가 급락에만 투입하는 것도 낙폭 억제의 한 축입니다.
    """)

    st.markdown("**2계층 구조**")
    st.markdown("""
| 계층 | 속도 | 성격 | 하는 일 |
|---|---|---|---|
| **레짐** (QQQ 200일선) | 느림 | 추세추종 | 지금이 순항할 계절인지 구조할 계절인지 판단 |
| **진입** (전일 −0.1% LOC) | 빠름 | 역추세 | 단기 눌림을 사서 반등에 되판다 (평균보유 **3.7일**) |
| **사이징** (비대칭 복리) | 12일 | 완충 | 이익 70% 재투자 / 손실 50%만 반영 |
    """)

    # ── 2. 모드 ──────────────────────────────────
    st.markdown("#### 2 · 두 모드 — 순항과 구조")
    st.markdown("""
이전 버전은 이 둘을 **'공격/방어'** 라고 불렀습니다. **그 이름이 틀렸습니다.**
""")
    cc = st.columns(2)
    for col, k in zip(cc, ['Offense', 'Safe']):
        m = MODE_META[k]; p = PARAMS[k]
        col.markdown(H(f"""
        <div class="dial" style="border-top:3px solid {m['color']};">
          <div style="font-size:1.15rem;font-weight:800;color:{m['color']};">
            {m['icon']} {m['ko']} <span class="bridge-en">{m['en']}</span></div>
          <div style="color:var(--mist);font-size:.84rem;margin:6px 0 10px;">{m['why']}</div>
          <div style="font-size:.9rem;margin-bottom:10px;"><b>{m['job']}</b></div>
          <table style="width:100%;font-size:.82rem;">
            <tr><td style="color:var(--mist)">분할</td>
                <td class="num" style="text-align:right"><b>{base_splits_for(k)}분할</b> (최대 {max_slots_for(k)})</td></tr>
            <tr><td style="color:var(--mist)">익절</td>
                <td class="num" style="text-align:right"><b>+{p['sell']}%</b></td></tr>
            <tr><td style="color:var(--mist)">시간손절</td>
                <td class="num" style="text-align:right"><b>{p['time']}일</b></td></tr>
            <tr><td style="color:var(--mist)">평균 노출</td>
                <td class="num" style="text-align:right;color:{m['color']}"><b>{m['expo_mean']:.1f}%</b></td></tr>
            <tr><td style="color:var(--mist)">95퍼센타일 노출</td>
                <td class="num" style="text-align:right;color:{m['color']}"><b>{m['expo_p95']:.1f}%</b></td></tr>
            <tr><td style="color:var(--mist)">최대 총노출</td>
                <td class="num" style="text-align:right;color:{m['color']}"><b>{m['expo_max_op']:.2f}× 운용원금</b></td></tr>
            <tr><td style="color:var(--mist)">시간 비중</td>
                <td class="num" style="text-align:right">{'82%' if k=='Offense' else '18%'}</td></tr>
          </table>
        </div>"""), unsafe_allow_html=True)

    st.markdown("")
    st.warning("""
**⚠️ 왜 '방어'라는 이름이 위험했나**

티어 크기 = 운용원금 ÷ 분할수 입니다. 그래서 **4분할(구조) 티어가 6분할(순항) 티어보다 큽니다.**
최대 총노출까지 계산하면 **구조 1.25× > 순항 1.17×** 로 완전히 역전됩니다.
실측 평균 노출도 구조 32.6% vs 순항 22.0%.

즉 이전의 '방어(Safe)'는 자본을 보존하는 모드가 **아니었습니다.**
익절 +0.01%(사실상 본전 탈출)로 오버솔드 반등을 빠르게 낚아채는 **약세장 스캘핑 모드**였고,
그래서 큰 포지션을 빨리 깔고 즉시 빠지는 게 논리적으로 일관됩니다.

**'구조(Rescue)'** 라는 이름이 이 성격을 정확히 담습니다 — 급하고, 위험지대로 들어가며,
목표가 이익이 아니라 **탈출**입니다.
    """)

    # ── 3. 성과 ──────────────────────────────────
    st.markdown("#### 3 · 성과 (2010-03 ~ 2026-07, 16.3년)")
    k = st.columns(4)
    k[0].markdown(dial("CAGR", "39.7%"), unsafe_allow_html=True)
    k[1].markdown(dial("MDD (실현)", "−29.3%", "기대는 −47% ⚠️"), unsafe_allow_html=True)
    k[2].markdown(dial("Calmar", "1.35", "B&H 0.47"), unsafe_allow_html=True)
    k[3].markdown(dial("승률", "79.8%", "PF 2.42"), unsafe_allow_html=True)
    st.markdown("")
    st.markdown("""
| 지표 | 떨사 200 | SOXL 매수후보유 |
|---|---|---|
| **Calmar** | **1.35** | 0.47 |
| CAGR | +39.7% | +42.2% |
| 최대낙폭 | **−29.3%** | −90.5% |
| 누적배수 | 232× | 310× |
| Sharpe / Sortino | 1.20 / 1.47 | — |

**해석.** 원금 기준으론 매수후보유가 더 법니다(310× vs 232×). 대신 **−90% 낙폭**을 견뎌야 하죠.
3배 ETF에서 −90%는 대부분의 사람이 실제로는 버티지 못하고 이탈하는 수준입니다.
**"실행 가능한 수익"** 관점에서는 낙폭을 1/3로 줄인 쪽이 결정적입니다.

**거래 프로파일** — 티어 1,826건 · 승률 79.8% · PF 2.42 · 평균보유 3.7일 ·
평균이익 \\$2,752 vs 평균손실 \\$4,499 (payoff 0.61) · 청산의 77.7%가 익절.
전형적인 **고승률·저페이오프** 구조로, **승률이 생명선**입니다.

**연도별** — 16년 중 손실 연도는 **2018년(−2.3%) 단 한 해**. 다만 이 압도적 일관성은
2010–26이 전부 반도체 강세장이었다는 사실과 분리해서 볼 수 없습니다.
    """)

    # ── 4. 몬테카를로 ────────────────────────────
    st.markdown("#### 4 · 몬테카를로 — 가장 중요한 발견")
    st.error("""
### 실현된 낙폭은 "운이 좋았던" 결과입니다

다른 검증들이 *"파라미터가 과적합인가"* 를 물었다면, 몬테카를로는 다른 질문에 답합니다 —
**"이 한 줄의 역사가 운이었나."** 16년 백테스트는 결국 **실현된 경로 하나**일 뿐입니다.
    """)
    st.markdown("""
**방법.** QQQ·SOXL 일간 로그수익률을 **같은 블록 인덱스로 동시 재표본**(두 자산의 상관과
레짐–가격 연결 보존), 20일 블록으로 변동성 클러스터링 유지, 합성 QQQ에서 200일선 레짐 재계산,
1,000회 시행. *(블록 길이 5~120일로 바꿔도 결론 동일 — 방법론 강건)*
    """)
    mc = st.columns(3)
    mc[0].markdown(dial("실현 MDD", "−30.5%", "상위 5% 행운"), unsafe_allow_html=True)
    mc[1].markdown(dial("기대 MDD (중앙값)", "−46.8%", "실제로 계획할 값"), unsafe_allow_html=True)
    mc[2].markdown(dial("기대 Calmar", "0.73", "실현 1.35 대비"), unsafe_allow_html=True)
    st.markdown("")
    st.markdown("""
| 지표 | 실현값 | 시뮬 중앙값 | 5th | 95th |
|---|---|---|---|---|
| CAGR | +38.3% | **+34.3%** | +23.2% | +43.6% |
| **MDD** | −30.5% | **−46.8%** ⚠️ | −30.0% | −74.8% |
| Calmar | 1.25 | **0.73** | 0.34 | 1.33 |

##### 꼬리 확률 — 반드시 인지할 것

| 사건 | 확률 |
|---|---|
| MDD가 −40%보다 나쁨 | **72%** |
| MDD가 −50%보다 나쁨 | **41%** |
| MDD가 −60%보다 나쁨 | **20%** |
| CAGR이 마이너스 | **0.1%** |
    """)
    g = st.columns(2)
    g[0].success("""
**엣지는 진짜입니다**

재표본된 1,000개 역사 중 **99.9%가 수익**으로 끝났습니다.
CAGR 중앙값 34.3%로 실현값과 근접합니다.
워크포워드·DSR 결론과 정확히 일치합니다.
    """)
    g[1].error("""
**낙폭은 운이었습니다**

실현 −30.5%는 **95%의 재표본 역사보다 운이 좋았던** 값입니다.
기대 낙폭은 **−47%**.

**→ 자금관리는 −45~50%를 전제로 하고,
5회 중 1회는 −60% 이상도 각오하세요.**
    """)

    # ── 5. 로버스트 검증 ─────────────────────────
    st.markdown("#### 5 · 로버스트 검증")
    st.markdown("""
| 검증 축 | 결과 | 판정 |
|---|---|---|
| **파라미터 민감도** | 배포값(+2.5%/8일) 주변 3×3 이웃 Calmar **1.25 ± 0.06** — 뾰족한 봉우리가 아니라 넓은 능선 | ✅ |
| **워크포워드 (IS 4년/OOS 1년)** | 고정 파라미터 **1.27** > 매년 재최적화 1.13 · 13폴드 중 11개 Calmar>1.0 | ✅ |
| **DSR (다중검정 보정)** | Sharpe 1.20 vs 디플레이션 벤치마크 SR₀ **0.06** → **DSR = 1.00** | ✅ |
| **CSCV (과적합확률)** | PBO 68%지만 IS-최고의 **OOS 붕괴율 0%**, 선택격차 −0.02 → 평평한 고원의 지문 | ✅ 양성 |
| **하위기간 일관성** | 2010–15: 1.10 / 2016–20: 1.18 / 2021–26: 1.87 | ✅ |
| **자산 교차검증 (TQQQ)** | 파라미터 그대로 이식 시 Calmar **0.74** (B&H 0.51 상회) · +2.5% 능선 공유 | ⚬ 부분 |
| **레짐 다양성** | 2010–26 전부 반도체 강세장 — 지속 약세장 미검증 | ⚠️ GAP |

**⚙️ 워크포워드의 핵심 시사점.** 매년 IS 최적값을 "똑똑하게" 다시 고른 워크포워드(1.13)가
**그냥 고정한 것(1.27)보다 못했습니다.** IS 최적 파라미터가 폴드마다 널뛰며(옵티마이저가 노이즈를 추종)
그 선택이 OOS에서 보상받지 못합니다. **파라미터를 고정하고 쫓지 마세요.**
+2.5%/8일이 "최적이라서" 좋은 게 아니라, **고원 위 아무 데나 앉아도 되기 때문에** 안전한 것입니다.

**🔬 자산 교차검증.** TQQQ(vol 61%)에 파라미터를 손대지 않고 이식해도 B&H를 위험조정 기준으로
이깁니다(0.74 > 0.51). 즉 SOXL 데이터 마이닝이 아니라 **레버리지 평균회귀라는 실재 구조**를
잡고 있습니다. 다만 크기는 변동성에 비례합니다 — **SOXL의 극단적 변동성은 버그가 아니라 연료**이고,
이 전략의 맞는 그릇이 고변동 자산인 이유입니다.
    """)

    # ── 6. 실패한 개선 ───────────────────────────
    st.markdown("#### 6 · 실패한 개선 시도 7종")
    st.caption("낙폭을 줄이려 시도했으나 검증에서 전부 탈락했습니다. 같은 실수를 반복하지 않기 위해 기록합니다.")
    st.markdown("""
| 시도 | Calmar | 왜 실패했나 |
|---|---|---|
| **기준선** | **1.35** | — |
| 하드 손절 −8% (구조 모드) | 0.95 | 반등 직전 바닥에서 손실을 확정 |
| 진입 컷오프 (이격 <−10%) | 1.17 | 가장 싼 급락 = 가장 좋은 반등 트레이드를 걸러버림 |
| 예비티어 제거 | 1.24 | 급락 매수 깊이가 곧 수익원 |
| 히스테리시스 밴드 (0.5%) | 1.21 | 휘프소는 줄지만 **MDD가 오히려 악화**(−29%→−33%) |
| 반도체 레짐 (SOXX 프록시) | 0.98 | 구조 모드 시간이 15%→27%로 급증 → 고노출 시간 증가 |
| 딥 사이징 (30일 −30% → 1.5배) | 1.03 | 이미 급락에 과노출인 구조 모드의 꼬리위험을 증폭 |
| 동파법 레짐 (주봉 RSI) | 0.92 | 구조 모드 시간이 18%→**55%** 로 3배 → MDD 폭발 |
    """)
    st.warning("""
**🔑 하나로 꿰는 결론**

이 전략의 엣지는 **반응성 있는 급락 매수 + 빠른 레짐 적응**입니다.
그것을 둔화시키는 어떤 장치도(스톱·컷오프·밴드·느린 레짐) **엣지 자체를 훼손합니다.**

그리고 마지막 세 개가 특히 중요합니다 — 레짐 필터를 바꾸면 **구조 모드에 머무는 시간**이 늘어나고,
구조는 고노출 모드이므로 낙폭이 커집니다. **레짐 필터는 그 아래 매매 로직과 짝을 이뤄
최적화된 것이지, 부품처럼 떼어 옮길 수 없습니다.**

**→ 기대 낙폭을 실제로 줄이는 길은 전략 내부에 없습니다.**
저상관 자산과의 포트폴리오 분산(매니지드 퓨처스 등)이 유일한 경로입니다.
    """)

    # ── 7. FAQ ───────────────────────────────────
    st.markdown("#### 7 · 예상 반론")
    with st.expander("Q1 · 200일선을 위아래로 횡보하면 어떤 모드가 적용되나요?"):
        st.markdown("""
버퍼 없이 **매일 종가 vs SMA200으로 하드하게 전환**됩니다. 16년간 전환 101회(연 6.2회),
전환 구간 중앙값 4거래일, 101개 중 55개가 5일 이하의 짧은 휘프소입니다.

다만 **각 티어는 진입 당시 모드의 규칙(birth_mode)을 끝까지 유지**하므로 모드가 뒤집혀도
보유 포지션은 흔들리지 않습니다. 신규 진입의 사이징·목표가만 바뀝니다.

**히스테리시스 밴드로 휘프소를 줄이면? — 오히려 나빠집니다.** 밴드 0.5%만 넣어도
Calmar 1.35→1.21, **MDD는 −29%→−33%로 악화**됩니다(CAGR은 불변). 밴드가 구조→순항 전환도
늦춰 반등 초입에 고노출 상태로 조정을 더 맞기 때문입니다. **반응 속도 자체가 방어 기제**였습니다.
        """)
    with st.expander("Q2 · 구조 모드의 분할 수 축소가 오히려 공격적으로 보이는데 괜찮나요?"):
        st.markdown("""
**정확한 지적이고, 그래서 이름을 바꿨습니다.** 구조 모드는 노출 기준으로 순항보다 공격적입니다
(평균 32.6% vs 22.0%, 최대 1.25× vs 1.17×).

이는 버그가 아니라 설계입니다. 구조는 익절 +0.01%(본전 스크래치)로 오버솔드 반등을 빠르게 잡아
빠지는 모드이므로, 큰 포지션을 빨리 깔고 즉시 터는 것이 일관됩니다. 대부분의 짧은 눌림에서는
빠른 스크래치 청산이 이 높은 노출을 상쇄합니다.

**문제는 반등이 오지 않을 때** — 그것이 Q3입니다.
        """)
    with st.expander("Q3 · 구조 모드에서 5티어가 모두 찬 뒤에도 계속 하락하면? ⚠️ 핵심 꼬리위험"):
        st.markdown("""
5티어 소진 후에는 **신규 매수가 정지되고 드라이파우더가 0**이 됩니다.
남은 티어는 +0.01% 반등(급락 중 거의 불가) 또는 10일 시간손절로만 청산되며,
**하드 프라이스 스톱이 없어 그동안 손실은 무제한**입니다.

**2025년 4월에 실제로 발생했습니다.** 노출이 84%까지 찬 상태에서 추가 급락하며
미실현 손실이 **−\\$349,377** 까지 벌어졌고, 총자산이 \\$1.19M → \\$0.85M로 빠졌습니다.

비대칭 복리(손실 50% 반영)가 이후 운용원금을 깎아 다음 사이클을 축소하지만,
이는 **한 박자 늦은 사후 대응**입니다. 이것이 몬테카를로가 경고하는 −47% 기대낙폭의 얼굴입니다.
        """)
    with st.expander("Q4 · 수익률을 더 높이려면 복리율을 조정하면 되나요?"):
        st.markdown("""
가능하지만 **공짜 점심이 아니라 순수 위험↔수익 교환**입니다.

| 이익 재투자 / 손실 반영 | CAGR | MDD | Calmar |
|---|---|---|---|
| **0.70 / 0.50 (현행)** | +39.7% | −29.4% | **1.35** |
| 0.80 / 0.50 | +45.7% | −34.6% | 1.32 |
| 1.00 / 1.00 | +45.0% | −37.3% | 1.21 |
| 0.80 / 0.30 | +50.2% | −39.4% | 1.28 |

복리율은 기존 위험–수익 직선 위를 이동시킬 뿐 **알파를 추가하지 않습니다**(분할 수 축소도 동일).
−40% 낙폭을 감당할 수 있으면 CAGR ~50%가 가능하지만, 3배 ETF에서 낙폭 확대는
**파산위험을 직접 키우고** 전략이 지키려던 생존성을 갉아먹습니다.
        """)

    st.divider()
    st.caption("떨사 200 v2.0 · 레버리지 ETF는 변동성 감쇠·경로의존 위험이 있으며 과거 성과는 미래를 보장하지 않습니다. "
               "본 문서는 검증 노트이며 투자권유가 아닙니다.")


# ============================================================================
#  양도세 / 재기자본
# ============================================================================
def _tax_section(wdf, today):
    dj = st.session_state.get('journal', pd.DataFrame())
    yr = {}
    if not dj.empty and '날짜' in dj.columns:
        try:
            t = dj.copy()
            t['_y'] = pd.to_datetime(t['날짜']).dt.year
            t['_p'] = pd.to_numeric(t['수익금'], errors='coerce').fillna(0)
            yr = t.groupby('_y')['_p'].sum().to_dict()
        except Exception:
            pass
    DED, RATE = 2_500_000 / 1400, 0.22
    ly = today.year - 1
    realized = float(yr.get(ly, 0.0))
    expect = max(0.0, realized - DED) * RATE
    paid = 0.0
    if wdf is not None and not wdf.empty:
        try:
            w = wdf.copy(); w['_y'] = pd.to_datetime(w['날짜']).dt.year
            paid = float(w[w['_y'] == today.year]['금액'].sum())
        except Exception:
            pass
    remain = max(0.0, expect - paid)
    alert = (5 <= today.month <= 8) and remain > 0

    with st.expander(("🔴  " if alert else "") + "양도세 인출 기록", expanded=alert):
        if alert:
            st.error(f"{ly}년분 양도세 — 예상 ${expect:,.0f} · 이미 인출 ${paid:,.0f} "
                     f"→ **남은 인출 ${remain:,.0f}** (신고 5/31, 분납 8/31)")
        ok = get_gspread_workbook() is not None
        if not ok:
            st.caption("시트 권한이 없어 저장할 수 없습니다 (GCP_CREDENTIALS 확인).")
        w = wdf.copy() if wdf is not None else pd.DataFrame(columns=["날짜","금액","메모"])
        if not w.empty:
            sm = w["메모"].apply(is_seed_memo)
            seed, tax = w[sm].copy(), w[~sm].copy()
        else:
            seed = pd.DataFrame(columns=["날짜","금액","메모"])
            tax  = pd.DataFrame(columns=["날짜","금액","메모"])
        disp = tax[["날짜","금액","메모"]] if not tax.empty else pd.DataFrame(
            {"날짜": pd.Series(dtype="datetime64[ns]"), "금액": pd.Series(dtype="float64"),
             "메모": pd.Series(dtype="object")})
        ed = st.data_editor(disp, num_rows="dynamic", use_container_width=True, key="wd_ed",
            column_config={"날짜": st.column_config.DateColumn("날짜", format="YYYY-MM-DD", required=True),
                           "금액": st.column_config.NumberColumn("금액 ($)", format="$%.2f", min_value=0.0),
                           "메모": st.column_config.TextColumn("메모")}, disabled=not ok)
        if st.button("인출 기록 저장", disabled=not ok):
            if save_tax_withdrawals(pd.concat([ed, seed[["날짜","금액","메모"]]], ignore_index=True)):
                st.session_state['auto_run_done'] = False
                try: st.cache_resource.clear()
                except Exception: pass
                st.rerun()


def _seed_section(wdf, ic, today):
    w = wdf.copy() if wdf is not None else pd.DataFrame(columns=["날짜","금액","메모"])
    if not w.empty:
        sm = w["메모"].apply(is_seed_memo)
        seed, tax = w[sm].copy(), w[~sm].copy()
    else:
        seed = pd.DataFrame(columns=["날짜","금액","메모"])
        tax  = pd.DataFrame(columns=["날짜","금액","메모"])
    de = st.session_state.get('equity_history', pd.DataFrame())
    try:
        asset = float(pd.to_numeric(de["총자산"], errors="coerce").dropna().iloc[-1]) \
                if not de.empty else float(ic)
    except Exception:
        asset = float(ic)
    vault = float(pd.to_numeric(seed["금액"], errors="coerce").sum()) if not seed.empty else 0.0
    if 'seed_anchor' not in st.session_state:
        st.session_state['seed_anchor'] = asset
    ref = seed_reference(seed, st.session_state['seed_anchor'])
    target = SEED_MULT * ref
    trig = asset >= target
    amt = SEED_PCT * asset
    new_ref = asset - amt

    with st.expander(("🟢  " if trig else "") + "재기자본 — 자산 2배마다 7% 인출", expanded=trig):
        if trig:
            st.success(f"자산 2배 도달 (${asset:,.0f} ≥ ${target:,.0f}) — "
                       f"**${amt:,.0f}** 인출 권장. SGOV 등 초단기국채로 옮기고 아래에 기록하세요. "
                       f"새 기준점 ${new_ref:,.0f}")
        else:
            st.caption(f"현재 ${asset:,.0f} · 다음 인출 기준 ${target:,.0f} "
                       f"(${max(0.0, target-asset):,.0f} 남음)")
        m = st.columns(3)
        m[0].markdown(dial("금고 누적", f"${vault:,.0f}", f"{len(seed)}회"), unsafe_allow_html=True)
        m[1].markdown(dial("현재 총자산", f"${asset:,.0f}"), unsafe_allow_html=True)
        m[2].markdown(dial("다음 2배 기준", f"${target:,.0f}"), unsafe_allow_html=True)
        st.write("")
        st.number_input("시작 기준점 ($)", step=100.0, key="seed_anchor")
        ok = get_gspread_workbook() is not None
        with st.form("seed_f"):
            f = st.columns(2)
            sdt = f[0].date_input("인출일", value=today)
            sam = f[1].number_input("인출 금액 ($)", value=round(amt, 2), step=10.0, min_value=0.0)
            smm = st.text_input("메모", value=f"재기씨앗 7% | ref={new_ref:.0f}")
            if st.form_submit_button("재기자본 인출 기록", disabled=not ok):
                if not is_seed_memo(smm):
                    smm = "재기씨앗 | " + smm
                nr = pd.DataFrame([{"날짜": pd.to_datetime(sdt), "금액": float(sam), "메모": smm}])
                if save_tax_withdrawals(pd.concat([tax[["날짜","금액","메모"]],
                                                   seed[["날짜","금액","메모"]], nr],
                                                  ignore_index=True)):
                    st.session_state['auto_run_done'] = False
                    try: st.cache_resource.clear()
                    except Exception: pass
                    st.rerun()
        st.caption("금고의 철칙 — SGOV 등 초단기국채·현금으로만 보관. 절대 SOXL로 되돌리지 않습니다.")


if __name__ == "__main__":
    main()
