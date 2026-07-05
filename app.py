# 파일명: ddulsa200_app.py  (배포 시 app.py 로 바꿔도 됨)
# ============================================================================
#  떨사 200 (Ddulsa 200) — 마스터 v1.0
# ----------------------------------------------------------------------------
#  · 대상: SOXL (반도체 3배 레버리지 ETF) / 레짐 판단: QQQ 200일선
#  · 핵심 로직(동파법과 다른 부분):
#      - 레짐 스위치: QQQ가 200일 이동평균선 '위' → 공격모드 / '아래' → 방어모드
#      - 공격모드: 6분할 균등 | 매수 전일종가 -0.1%↓(LOC) | 익절 +2.5% | 8거래일 손절
#      - 방어모드: 4분할 균등 | 매수 전일종가 -0.1%↓(LOC) | 익절 +0.01% | 10거래일 손절
#      - 예비티어: 6분할(공격)/4분할(방어) 소진 시 +1 예비티어까지 매수
#      - 비대칭 복리: 12거래일마다 운용원금 재산정 — 이익 80% 재투자 / 손실 30%만 반영
#  · 동파법과 동일하게 유지: 3탭 구성, GitHub/시트 연동, 배당 재투자,
#      백테스트 수수료·양도세(A/B) 시뮬, 상세 매매로그, 연도별 성과표, 재기자본 인출.
#  · 자산 마킹: 매일 real_cash + 보유평가액 (증권사 기준 정확 마킹)
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
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

# 선택적 의존성 (없어도 로컬/백테스트는 동작)
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

st.set_page_config(page_title="떨사 200 마스터 v1.0", page_icon="📉", layout="wide")
st.markdown("""
<style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css");
    html, body, [class*="css"] { font-family: 'Pretendard', sans-serif; }
    .st-card { background-color: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e0e0e0; margin-bottom: 15px; }
    @media (prefers-color-scheme: dark) { .st-card { background-color: #262730; border: 1px solid #41424b; } }
    .badge-buy  { background-color: #e6f4ea; color: #1e8e3e; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; }
    .badge-sell { background-color: #fce8e6; color: #d93025; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; }
    .badge-off  { background-color: #fce8e6; color: #d93025; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    .badge-def  { background-color: #e8f0fe; color: #1a73e8; padding: 6px 12px; border-radius: 6px; font-weight: bold; }
    div[data-testid="stMetric"] { background-color: rgba(255,255,255,0.05); border: 1px solid rgba(128,128,128,0.2); padding: 15px; border-radius: 10px; text-align: center; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
#  전략 파라미터 (떨사 200)
# ============================================================================
# 화면 표시용(퍼센트 단위)
PARAMS = {
    'Safe':    {'buy': -0.1, 'sell': 0.01, 'time': 10, 'desc': '🛡️ 방어 (Safe)'},
    'Offense': {'buy': -0.1, 'sell': 2.5,  'time': 8,  'desc': '⚔️ 공격 (Offense)'},
}
# 내부 계산용: buy=전일종가 대비 비율(-0.001=-0.1%), sell=목표 배수, time=손절 거래일
LOCAL_PARAMS = {
    'Safe':    {'buy': -0.001, 'sell': 1.0001, 'time': 10},
    'Offense': {'buy': -0.001, 'sell': 1.025,  'time': 8},
}
BASE_SPLITS = {'Offense': 6, 'Safe': 4}   # 균등 분할 수 (1티어 = 원금/분할수)
MAX_SLOTS   = {'Offense': 7, 'Safe': 5}   # 예비티어 포함 최대 동시 보유
RESET_CYCLE = 12                          # 비대칭 복리 재산정 주기(거래일)
UP_RATE     = 0.80                        # 상승 복리율 (이익의 80% 재투자)
DN_RATE     = 0.30                        # 하락 복리율 (손실의 30%만 반영)
QREG_SMA_WINDOW = 200                     # 레짐 판단 이동평균(일)

def base_splits_for(mode): return BASE_SPLITS.get(mode, 4)
def max_slots_for(mode):   return MAX_SLOTS.get(mode, 5)

# ============================================================================
#  저장소 설정 (동파법과 분리된 파일명 — 원하면 동일 파일명으로 바꿔 공유 가능)
# ============================================================================
def _secret(section, key, default=None):
    """st.secrets[section][key] 안전 조회 (없으면 default)."""
    try:
        return st.secrets[section][key]
    except Exception:
        return default

# GitHub (실전 트레이딩 저장용) — 없으면 백테스트만 동작
GH_TOKEN      = _secret("general", "GH_TOKEN")                 # Personal Access Token
REPO_KEY      = _secret("general", "REPO_KEY", "yongma11/dongpa6")  # ← 본인 새 repo 로 설정
HOLDINGS_FILE = "ddulsa200_holdings.csv"
JOURNAL_FILE  = "ddulsa200_journal.csv"
EQUITY_FILE   = "ddulsa200_equity.csv"
SETTINGS_FILE = "ddulsa200_settings.json"
# Google Sheets (양도세/재기자본 인출용, 선택)
SPREADSHEET_ID        = _secret("general", "SPREADSHEET_ID",
                                "1s8XX-8PUAWyWOHOwst2W-b99pQo1_aFtLVg5uTD_HMI")
WITHDRAWAL_SHEET_NAME = _secret("general", "WITHDRAWAL_SHEET_NAME", "TaxWithdrawals_DDULSA200")

def get_now_kst():
    return datetime.utcnow() + timedelta(hours=9)

# ============================================================================
#  데이터
# ============================================================================
@st.cache_data(ttl=600)
def get_data_final(period='max'):
    """QQQ / SOXL 종가(split 반영, dividend 미반영) + SOXL 배당 컬럼."""
    for attempt in range(3):
        try:
            start_date   = '2005-01-01'
            end_date_str = (get_now_kst() + timedelta(days=1)).strftime('%Y-%m-%d')
            df_qqq  = yf.download("QQQ",  start=start_date, end=end_date_str,
                                  progress=False, auto_adjust=False, actions=True)
            df_soxl = yf.download("SOXL", start=start_date, end=end_date_str,
                                  progress=False, auto_adjust=False, actions=True)
            if df_qqq.empty or df_soxl.empty:
                time.sleep(1); continue
            qqq_close  = df_qqq['Close']['QQQ']   if isinstance(df_qqq.columns,  pd.MultiIndex) else df_qqq['Close']
            soxl_close = df_soxl['Close']['SOXL'] if isinstance(df_soxl.columns, pd.MultiIndex) else df_soxl['Close']
            try:
                soxl_div = df_soxl['Dividends']['SOXL'] if isinstance(df_soxl.columns, pd.MultiIndex) else df_soxl['Dividends']
                soxl_div = soxl_div.fillna(0).astype(float)
            except (KeyError, AttributeError):
                soxl_div = pd.Series(0.0, index=soxl_close.index)
            df = pd.DataFrame({'QQQ': qqq_close, 'SOXL': soxl_close, 'SOXL_Div': soxl_div})
            df['SOXL_Div'] = df['SOXL_Div'].fillna(0)
            df = df.sort_index().dropna(subset=['QQQ', 'SOXL'])
            df['QQQ']  = df['QQQ'].ffill().bfill()
            df['SOXL'] = df['SOXL'].ffill().bfill()
            df.index = df.index.tz_localize(None)
            return df
        except Exception:
            time.sleep(1)
    return None

def calc_mode_series(qqq_series):
    """QQQ 200일선 기준 일별 모드. QQQ>SMA200 → Offense(공격), else Safe(방어).
    반환: (mode_daily, dist_daily[이격 %])"""
    sma = qqq_series.rolling(QREG_SMA_WINDOW, min_periods=QREG_SMA_WINDOW // 2).mean()
    mode = pd.Series(np.where(qqq_series.values > sma.values, 'Offense', 'Safe'),
                     index=qqq_series.index)
    mode[sma.isna().values] = 'Safe'
    dist = (qqq_series / sma - 1.0) * 100.0
    return mode, dist

def calc_qqq_ma_frame(df):
    """백테스트 로그용: QQQ / SMA200 / 이격%."""
    price = df['QQQ']
    sma   = price.rolling(QREG_SMA_WINDOW, min_periods=QREG_SMA_WINDOW // 2).mean()
    dist  = (price / sma - 1.0) * 100.0
    return pd.DataFrame({'QQQ': price, 'QQQ_SMA200': sma, 'QQQ_Dist': dist})

# ============================================================================
#  GitHub / 설정 / CSV
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
            contents = repo.get_contents(SETTINGS_FILE)
            return json.loads(contents.decoded_content.decode("utf-8"))
    except Exception:
        pass
    return {"start_date": "2026-01-23", "init_cap": 100000.0}

def save_settings(settings_dict):
    try:
        repo = get_repo()
        js = json.dumps(settings_dict)
        if repo:
            try:
                contents = repo.get_contents(SETTINGS_FILE)
                repo.update_file(contents.path, "Update settings", js, contents.sha)
            except Exception:
                repo.create_file(SETTINGS_FILE, "Create settings", js)
    except Exception as e:
        print(f"설정 저장 실패: {e}")

def load_csv(filename, columns):
    try:
        repo = get_repo()
        if repo:
            try:
                contents = repo.get_contents(filename)
                return pd.read_csv(StringIO(contents.decoded_content.decode("utf-8")))
            except Exception:
                pass
    except Exception:
        pass
    return pd.DataFrame(columns=columns)

def save_csv(df, filename):
    try:
        repo = get_repo()
        csv_string = df.to_csv(index=False)
        if repo:
            try:
                contents = repo.get_contents(filename)
                repo.update_file(contents.path, f"Update {filename}", csv_string, contents.sha)
            except Exception:
                repo.create_file(filename, f"Create {filename}", csv_string)
    except Exception as e:
        st.error(f"GitHub 저장 실패: {e}")

# ============================================================================
#  Google Sheets (양도세/재기자본 인출)
# ============================================================================
@st.cache_resource
def get_gspread_workbook():
    if not _HAS_GS:
        return None
    try:
        creds_raw = st.secrets["general"]["GCP_CREDENTIALS"]
    except Exception:
        return None
    try:
        creds_dict = json.loads(creds_raw) if isinstance(creds_raw, str) else dict(creds_raw)
        scopes = ['https://www.googleapis.com/auth/spreadsheets',
                  'https://www.googleapis.com/auth/drive']
        creds  = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    except Exception as e:
        print(f"⚠️ gspread 오픈 실패: {e}")
        return None

def is_seed_memo(memo):
    m = str(memo or "")
    return ("재기" in m) or ("씨앗" in m) or ("seed" in m.lower())

def parse_seed_ref(memo):
    mm = re.search(r"ref\s*=\s*([0-9]+(?:\.[0-9]+)?)", str(memo or ""))
    return float(mm.group(1)) if mm else None

SEED_PCT  = 0.07
SEED_MULT = 2.0

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
    except Exception:
        return pd.DataFrame(columns=cols)
    try:
        rows = ws.get_all_records()
    except Exception:
        return pd.DataFrame(columns=cols)
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    rename_map = {}
    for c in df.columns:
        key = str(c).strip()
        if key in ("날짜", "Date", "date"):              rename_map[c] = "날짜"
        elif key in ("금액", "Amount", "amount", "USD"): rename_map[c] = "금액"
        elif key in ("메모", "Memo", "memo", "Note"):    rename_map[c] = "메모"
    df = df.rename(columns=rename_map)
    for c in cols:
        if c not in df.columns:
            df[c] = "" if c == "메모" else None
    df["날짜"] = pd.to_datetime(df["날짜"], errors='coerce')
    df["금액"] = pd.to_numeric(df["금액"], errors='coerce').fillna(0.0)
    df = df.dropna(subset=["날짜"])
    df = df[df["금액"] > 0].copy().sort_values("날짜").reset_index(drop=True)
    return df[cols]

def save_tax_withdrawals(df):
    wb = get_gspread_workbook()
    if wb is None:
        st.error("⚠️ GCP_CREDENTIALS 미설정 — 시트에 저장할 수 없습니다.")
        return False
    try:
        ws = wb.worksheet(WITHDRAWAL_SHEET_NAME)
    except Exception:
        try:
            ws = wb.add_worksheet(title=WITHDRAWAL_SHEET_NAME, rows=200, cols=5)
        except Exception as e:
            st.error(f"⚠️ 시트 탭 생성 실패: {e}")
            return False
    header = ["날짜", "금액", "메모"]
    rows = [header]
    dfc = df.copy() if df is not None else pd.DataFrame(columns=header)
    if not dfc.empty and "날짜" in dfc.columns:
        dfc = dfc.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
        for _, row in dfc.iterrows():
            try:
                dv = pd.to_datetime(row.get("날짜"))
                if pd.isna(dv):
                    continue
                amt = float(row.get("금액") or 0)
                if amt <= 0:
                    continue
                rows.append([dv.strftime("%Y-%m-%d"), round(amt, 2), str(row.get("메모", "") or "")])
            except Exception:
                continue
    try:
        ws.clear(); ws.update(rows); return True
    except Exception as e:
        st.error(f"⚠️ 시트 쓰기 실패: {e}")
        return False

# ============================================================================
#  실전 동기화 엔진 (전략 시작일 이후 자동 시뮬)
# ============================================================================
def auto_sync_engine(df, start_date, init_cap, withdrawals_df=None):
    empty = (None, None, None, None, None, 1, 0.0, 0.0, 'Safe', 0.0)
    if df is None:
        return empty
    mode_daily, dist_daily = calc_mode_series(df['QQQ'])
    sim_df = pd.concat([df['SOXL'], df['SOXL_Div'], mode_daily, dist_daily], axis=1)
    sim_df.columns = ['Price', 'Div', 'Mode', 'Dist']
    sim_df['Div'] = sim_df['Div'].fillna(0)
    sim_df = sim_df.dropna(subset=['Price', 'Mode'])
    end_date = get_now_kst() - timedelta(days=1)
    mask = (sim_df.index >= pd.to_datetime(start_date)) & (sim_df.index <= pd.to_datetime(end_date.date()))
    sim_df = sim_df[mask]
    if sim_df.empty:
        return empty
    sim_df['Prev_Price'] = sim_df['Price'].shift(1)
    sim_df['Prev_Mode']  = sim_df['Mode'].shift(1)
    sim_df = sim_df.dropna(subset=['Prev_Price', 'Prev_Mode'])

    wd_queue = []
    if withdrawals_df is not None and not withdrawals_df.empty:
        wd_queue = [(pd.Timestamp(r["날짜"]).normalize(), float(r["금액"]), str(r.get("메모", "") or ""))
                    for _, r in withdrawals_df.iterrows()]
    wd_idx = 0

    real_cash     = init_cap
    op_base       = init_cap
    cum_net       = 0.0
    last_cycle    = 0.0
    cum_withdrawn = 0.0
    cum_dividends = 0.0
    slots, journal, daily_equity, full_action_log = [], [], [], []
    cycle_days = 0
    slot_sizes = {'Offense': init_cap / base_splits_for('Offense'),
                  'Safe':    init_cap / base_splits_for('Safe')}

    for date, row in sim_df.iterrows():
        price, div_amt = row['Price'], float(row.get('Div', 0) or 0)
        prev_price, prev_mode = row['Prev_Price'], row['Prev_Mode']

        if div_amt > 0:
            held_shares = sum(s['shares'] for s in slots)
            if held_shares > 0:
                dc = div_amt * held_shares
                real_cash += dc; cum_dividends += dc
                full_action_log.append({"날짜": date.date(), "구분": "💰 배당 입금",
                    "가격": f"${div_amt:.4f}", "수량": held_shares, "수익금": f"+${dc:,.2f}",
                    "비고": f"SOXL ex-div × {held_shares}주 (재투자)"})

        # 매도
        sold_idx = []
        for i in range(len(slots) - 1, -1, -1):
            s = slots[i]; s['days'] += 1
            rule = LOCAL_PARAMS.get(s['birth_mode'], LOCAL_PARAMS['Safe'])
            if (price >= s['buy_price'] * rule['sell']) or (s['days'] >= rule['time']):
                rev  = s['shares'] * price
                prof = rev - s['shares'] * s['buy_price']
                other = sum(slots[k]['shares'] * price for k in range(len(slots)) if k != i)
                eq_at = real_cash + rev + other
                journal.append({"날짜": date.date(), "총자산": eq_at, "수익금": prof,
                                "수익률": (prof / (eq_at - prof) * 100) if (eq_at - prof) > 0 else 0})
                full_action_log.append({"날짜": date.date(), "구분": "매도 (Sell)", "가격": f"${price:.2f}",
                    "수량": s['shares'], "수익금": f"${prof:.2f}",
                    "비고": ("익절" if price >= s['buy_price'] * rule['sell'] else "기간만료 MOC")})
                real_cash += rev; cum_net += prof
                sold_idx.append(i)
        for i in sold_idx:
            del slots[i]

        # 비대칭 복리 (매수 전에 갱신)
        cycle_days += 1
        if cycle_days >= RESET_CYCLE:
            cyc = cum_net - last_cycle
            op_base += (UP_RATE * cyc) if cyc >= 0 else (DN_RATE * cyc)
            last_cycle = cum_net
            op_base = max(op_base, 1000.0)
            slot_sizes['Offense'] = op_base / base_splits_for('Offense')
            slot_sizes['Safe']    = op_base / base_splits_for('Safe')
            cycle_days = 0

        # 매수 (전일 모드 기준 — 룩어헤드 방지)
        curr_rule = LOCAL_PARAMS.get(prev_mode, LOCAL_PARAMS['Safe'])
        loc_price = prev_price * (1 + curr_rule['buy'])
        if price <= loc_price and len(slots) < max_slots_for(prev_mode):
            amt = min(real_cash, slot_sizes[prev_mode])
            shares = int(amt / loc_price)
            if shares > 0:
                real_cash -= shares * price
                tr = PARAMS[prev_mode]
                slots.append({'매수일': date.date(), '모드': prev_mode, '매수가': price, '수량': shares,
                              '목표가': price * (1 + tr['sell'] / 100),
                              '손절기한': (date + timedelta(days=int(tr['time'] * 1.45))).date(),
                              'buy_price': price, 'shares': shares, 'days': 0, 'birth_mode': prev_mode})
                full_action_log.append({"날짜": date.date(), "구분": "매수 (Buy)", "가격": f"${price:.2f}",
                    "수량": shares, "수익금": "-",
                    "비고": f"{prev_mode} 진입 @LOC ${loc_price:.2f}"})

        # 외부 인출
        while wd_idx < len(wd_queue) and wd_queue[wd_idx][0] <= pd.Timestamp(date).normalize():
            wa, wm = wd_queue[wd_idx][1], wd_queue[wd_idx][2]
            real_cash -= wa; cum_withdrawn += wa
            full_action_log.append({"날짜": date.date(),
                "구분": ("🌱 재기자본 인출" if is_seed_memo(wm) else "💸 양도세 인출"),
                "가격": "-", "수량": "-", "수익금": f"-${wa:,.2f}",
                "비고": f"외부 출금{(' — ' + wm) if wm else ''}"})
            wd_idx += 1

        daily_equity.append({"날짜": date.date(),
                             "총자산": real_cash + sum(s['shares'] * price for s in slots)})

    final_holdings = [{"매수일": s['매수일'], "모드": s['모드'], "매수가": s['매수가'], "수량": s['수량'],
                       "목표가": s['목표가'], "손절기한": s['손절기한']} for s in slots]
    df_actions = pd.DataFrame(full_action_log)
    if not df_actions.empty:
        df_actions = df_actions.sort_values(by="날짜", ascending=False).reset_index(drop=True)
    today_mode = mode_daily.iloc[-1] if not mode_daily.empty else 'Safe'
    today_dist = float(dist_daily.iloc[-1]) if not dist_daily.dropna().empty else 0.0
    today_slot = slot_sizes.get(today_mode, slot_sizes['Safe'])
    return (pd.DataFrame(final_holdings), pd.DataFrame(journal), pd.DataFrame(daily_equity),
            df_actions, today_slot, (cycle_days % RESET_CYCLE) + 1,
            cum_withdrawn, cum_dividends, today_mode, today_dist)

# ============================================================================
#  백테스트 엔진
# ============================================================================
def run_backtest_fixed(df, start_date, end_date, init_cap,
                       include_fees=False, include_tax=False,
                       buy_fee_rate=0.00015, sell_fee_rate=0.0001706,
                       tax_deduction_usd=1786.0, tax_rate=0.22,
                       tax_strategy='A', custom_schedule=None):
    TAX_SCHEDULES = {'A': [(1.00, (5, 1), (5, 31))]}
    if tax_strategy == 'B' and custom_schedule:
        tax_tranches_def = custom_schedule
    else:
        tax_tranches_def = TAX_SCHEDULES['A']
    if df is None:
        return None, None, None, None

    mode_daily, dist_daily = calc_mode_series(df['QQQ'])
    qmaf = calc_qqq_ma_frame(df)
    sim_df = pd.concat([df['SOXL'], df['SOXL_Div'], mode_daily, dist_daily,
                        qmaf['QQQ'], qmaf['QQQ_SMA200'], qmaf['QQQ_Dist']], axis=1)
    sim_df.columns = ['Price', 'Div', 'Mode', 'Dist', 'QQQ', 'QQQ_SMA200', 'QQQ_Dist']
    sim_df['Div'] = sim_df['Div'].fillna(0)
    sim_df = sim_df.dropna(subset=['Price', 'Mode'])
    mask = (sim_df.index >= pd.to_datetime(start_date)) & (sim_df.index <= pd.to_datetime(end_date))
    sim_df = sim_df[mask]
    if sim_df.empty:
        return None, None, None, None
    sim_df['Prev_Price'] = sim_df['Price'].shift(1)
    sim_df['Prev_Mode']  = sim_df['Mode'].shift(1)
    sim_df = sim_df.dropna(subset=['Prev_Price', 'Prev_Mode'])

    real_cash   = init_cap
    op_base     = init_cap
    cum_net     = 0.0
    last_cycle  = 0.0
    cum_dividends = 0.0
    slots, equity_curve, debug_logs = [], [], []
    gross_profit = gross_loss = 0.0
    cycle_days = 0
    slot_sizes = {'Offense': init_cap / base_splits_for('Offense'),
                  'Safe':    init_cap / base_splits_for('Safe')}
    total_buy_fees = total_sell_fees = 0.0
    annual_realized = 0.0
    total_tax_paid = 0.0
    last_year_seen = None
    tax_log = []
    yearly_realized_log, yearly_fee_log, yearly_tax_log, yearly_div_log = {}, {}, {}, {}
    pending_tranches = []
    forced_count = negative_cash_days = 0

    for date, row in sim_df.iterrows():
        price, div_amt = row['Price'], float(row.get('Div', 0) or 0)
        prev_price, prev_mode = row['Prev_Price'], row['Prev_Mode']
        qqq_close = row.get('QQQ', np.nan)
        qqq_sma   = row.get('QQQ_SMA200', np.nan)
        qqq_dist  = row.get('QQQ_Dist', np.nan)
        cur_year  = date.year

        if div_amt > 0:
            held_shares = sum(s['shares'] for s in slots)
            if held_shares > 0:
                dc = div_amt * held_shares
                real_cash += dc; cum_dividends += dc
                yearly_div_log[cur_year] = yearly_div_log.get(cur_year, 0.0) + dc

        # 연도 전환 → 양도세 트랜치 예약
        if last_year_seen is not None and cur_year != last_year_seen:
            yearly_realized_log[last_year_seen] = annual_realized
            if include_tax:
                annual_tax = max(0.0, annual_realized - tax_deduction_usd) * tax_rate
                if annual_tax > 0:
                    for entry in tax_tranches_def:
                        if len(entry) == 4:
                            frac, (em, ed), (fm, fd), yoff = entry
                        else:
                            frac, (em, ed), (fm, fd) = entry; yoff = 0
                        if yoff == -1:
                            tax_due = annual_tax * frac
                            actual = min(tax_due, max(0.0, real_cash))
                            if actual > 0:
                                real_cash -= actual; total_tax_paid += actual
                                yearly_tax_log[last_year_seen] = yearly_tax_log.get(last_year_seen, 0.0) + actual
                                tax_log.append((date, actual, 'dec_anticipated'))
                            remaining = tax_due - actual
                            if remaining > 1e-6:
                                pending_tranches.append({'amount': remaining,
                                    'earliest': pd.Timestamp(year=cur_year, month=1, day=1),
                                    'force': pd.Timestamp(year=cur_year, month=1, day=31),
                                    'paid': 0.0, 'year': last_year_seen})
                        else:
                            pending_tranches.append({'amount': annual_tax * frac,
                                'earliest': pd.Timestamp(year=cur_year, month=em, day=ed),
                                'force': pd.Timestamp(year=cur_year, month=fm, day=fd),
                                'paid': 0.0, 'year': last_year_seen})
            annual_realized = 0.0
        last_year_seen = cur_year

        # 매도
        sold_idx, sold_qty_total, sold_pnl_total = [], 0, 0.0
        for i in range(len(slots) - 1, -1, -1):
            s = slots[i]; s['days'] += 1
            rule = LOCAL_PARAMS.get(s['birth_mode'], LOCAL_PARAMS['Safe'])
            if (price >= s['buy_price'] * rule['sell']) or (s['days'] >= rule['time']):
                gross_rev = s['shares'] * price
                sell_fee  = gross_rev * sell_fee_rate if include_fees else 0.0
                net_rev   = gross_rev - sell_fee
                cost_basis = s.get('cost_basis', s['shares'] * s['buy_price'])
                prof = net_rev - cost_basis
                real_cash += net_rev; cum_net += prof
                total_sell_fees += sell_fee
                yearly_fee_log[cur_year] = yearly_fee_log.get(cur_year, 0.0) + sell_fee
                annual_realized += prof
                if prof > 0: gross_profit += prof
                else:        gross_loss += abs(prof)
                sold_idx.append(i); sold_qty_total += s['shares']; sold_pnl_total += prof
        for i in sold_idx:
            del slots[i]
        if sold_qty_total > 0:
            alloc = sum(s['shares'] * price for s in slots)
            ta = real_cash + alloc
            debug_logs.append({"날짜": date.date(), "Action": "🔴 매도", "적용모드": prev_mode,
                "종가": f"${price:.2f}", "수량": f"{-sold_qty_total:+,d}",
                "실현손익": f"${sold_pnl_total:,.2f}",
                "Balance_Qty": f"{sum(s['shares'] for s in slots):,d}",
                "Total_Cash": f"${real_cash:,.0f}", "Allocated_Cap": f"${alloc:,.0f}",
                "Total_Asset": f"${ta:,.0f}", "Return_Pct": f"{(ta/init_cap-1)*100:+.2f}%",
                "op_base": f"${op_base:,.0f}",
                "QQQ종가": f"{qqq_close:.2f}" if pd.notna(qqq_close) else "",
                "QQQ_200MA": f"{qqq_sma:.2f}" if pd.notna(qqq_sma) else "",
                "이격%": f"{qqq_dist:+.1f}" if pd.notna(qqq_dist) else ""})

        # 비대칭 복리 (매수 전 갱신)
        cycle_days += 1
        if cycle_days >= RESET_CYCLE:
            cyc = cum_net - last_cycle
            op_base += (UP_RATE * cyc) if cyc >= 0 else (DN_RATE * cyc)
            last_cycle = cum_net
            op_base = max(op_base, 1000.0)
            slot_sizes['Offense'] = op_base / base_splits_for('Offense')
            slot_sizes['Safe']    = op_base / base_splits_for('Safe')
            cycle_days = 0

        # 매수
        curr_rule = LOCAL_PARAMS.get(prev_mode, LOCAL_PARAMS['Safe'])
        loc_price = prev_price * (1 + curr_rule['buy'])
        if price <= loc_price and len(slots) < max_slots_for(prev_mode):
            amt = min(real_cash, slot_sizes[prev_mode])
            shares = int(amt / loc_price)
            if shares > 0:
                invested = shares * price
                buy_fee  = invested * buy_fee_rate if include_fees else 0.0
                cost_basis = invested + buy_fee
                real_cash -= cost_basis
                total_buy_fees += buy_fee
                yearly_fee_log[cur_year] = yearly_fee_log.get(cur_year, 0.0) + buy_fee
                slots.append({'buy_price': price, 'shares': shares, 'days': 0,
                              'birth_mode': prev_mode, 'cost_basis': cost_basis})
                alloc = sum(s['shares'] * price for s in slots)
                ta = real_cash + alloc
                debug_logs.append({"날짜": date.date(), "Action": "🟢 매수", "적용모드": prev_mode,
                    "종가": f"${price:.2f}", "수량": f"+{shares:,d}", "실현손익": "$0.00",
                    "Balance_Qty": f"{sum(s['shares'] for s in slots):,d}",
                    "Total_Cash": f"${real_cash:,.0f}", "Allocated_Cap": f"${alloc:,.0f}",
                    "Total_Asset": f"${ta:,.0f}", "Return_Pct": f"{(ta/init_cap-1)*100:+.2f}%",
                    "op_base": f"${op_base:,.0f}",
                    "QQQ종가": f"{qqq_close:.2f}" if pd.notna(qqq_close) else "",
                    "QQQ_200MA": f"{qqq_sma:.2f}" if pd.notna(qqq_sma) else "",
                    "이격%": f"{qqq_dist:+.1f}" if pd.notna(qqq_dist) else ""})

        equity_curve.append({'Date': date, 'Equity': real_cash + sum(s['shares'] * price for s in slots)})

        # 양도세 실제 인출 처리
        is_cycle_end = (cycle_days == 0)
        for tranche in pending_tranches:
            remaining = tranche['amount'] - tranche['paid']
            if remaining <= 1e-6:
                continue
            if date >= tranche['force']:
                real_cash -= remaining; total_tax_paid += remaining; tranche['paid'] += remaining
                yearly_tax_log[cur_year] = yearly_tax_log.get(cur_year, 0.0) + remaining
                tax_log.append((date, remaining, 'force')); forced_count += 1
            elif date >= tranche['earliest'] and is_cycle_end:
                actual = min(remaining, max(0.0, real_cash))
                if actual > 0:
                    real_cash -= actual; total_tax_paid += actual; tranche['paid'] += actual
                    yearly_tax_log[cur_year] = yearly_tax_log.get(cur_year, 0.0) + actual
                    tax_log.append((date, actual, 'cycle'))
        pending_tranches = [t for t in pending_tranches if (t['amount'] - t['paid']) > 1e-6]
        if real_cash < 0:
            negative_cash_days += 1

    if last_year_seen is not None:
        yearly_realized_log.setdefault(last_year_seen, annual_realized)
    pending_unrealized = max(0.0, annual_realized - tax_deduction_usd) * tax_rate if include_tax else 0.0
    pending_unfunded = sum(t['amount'] - t['paid'] for t in pending_tranches)
    pending_tax_at_end = pending_unrealized + pending_unfunded

    res_df = pd.DataFrame(equity_curve).set_index('Date')
    df_debug = pd.DataFrame(debug_logs).reset_index(drop=True) if debug_logs else pd.DataFrame()

    if not res_df.empty:
        res_df['Returns'] = res_df['Equity'].pct_change()
        downside = res_df.loc[res_df['Returns'] < 0, 'Returns']
        downside_std = downside.std() * np.sqrt(252)
        total_ret = (res_df['Equity'].iloc[-1] / init_cap) - 1
        days = (res_df.index[-1] - res_df.index[0]).days
        cagr = (1 + total_ret) ** (365 / days) - 1 if days > 0 else 0
        sortino = cagr / downside_std if downside_std > 0 else 0
        metrics = {'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 99.9,
                   'sortino': sortino, 'total_buy_fees': total_buy_fees,
                   'total_sell_fees': total_sell_fees, 'total_fees': total_buy_fees + total_sell_fees,
                   'total_tax_paid': total_tax_paid, 'tax_pending_end': pending_tax_at_end,
                   'tax_log': tax_log, 'include_fees': include_fees, 'include_tax': include_tax,
                   'tax_strategy': tax_strategy, 'forced_count': forced_count,
                   'negative_cash_days': negative_cash_days, 'total_dividends': cum_dividends}
    else:
        metrics = {'profit_factor': 0, 'sortino': 0, 'total_buy_fees': 0, 'total_sell_fees': 0,
                   'total_fees': 0, 'total_tax_paid': 0, 'tax_pending_end': 0, 'tax_log': [],
                   'include_fees': include_fees, 'include_tax': include_tax, 'tax_strategy': tax_strategy,
                   'forced_count': 0, 'negative_cash_days': 0, 'total_dividends': 0}

    def calc_mdd(series):
        peak = series.cummax()
        return ((series - peak) / peak).min()
    yearly_stats = []
    prev_equity = init_cap
    for yr in res_df.index.year.unique():
        df_yr = res_df[res_df.index.year == yr]
        end_equity = df_yr['Equity'].iloc[-1]
        yearly_stats.append({"연도": yr, "수익률": (end_equity - prev_equity) / prev_equity,
                             "MDD": calc_mdd(df_yr['Equity']), "기말자산": end_equity,
                             "수수료": yearly_fee_log.get(yr, 0.0), "양도세": yearly_tax_log.get(yr, 0.0),
                             "실현손익": yearly_realized_log.get(yr, 0.0), "배당": yearly_div_log.get(yr, 0.0)})
        prev_equity = end_equity
    return res_df, metrics, pd.DataFrame(yearly_stats).set_index("연도"), df_debug

# ============================================================================
#  메인 UI
# ============================================================================
def mode_badge(mode):
    return '<span class="badge-off">⚔️ 공격 (200일선 위)</span>' if mode == 'Offense' \
        else '<span class="badge-def">🛡️ 방어 (200일선 아래)</span>'

def main():
    st.title("📉 떨사 200 마스터 v1.0")
    st.caption("QQQ 200일선 레짐 × SOXL 분할매수 × 비대칭 복리")
    tab_trade, tab_backtest, tab_logic = st.tabs(["💎 실전 트레이딩", "🧪 백테스트", "📚 전략 로직"])

    with st.spinner("데이터 로딩 중... (3회 재시도)"):
        df = get_data_final()
    offline_mode = df is None
    if offline_mode:
        st.warning("⚠️ **오프라인 모드:** 현재가 업데이트 중단.")

    if not offline_mode:
        mode_s, dist_s = calc_mode_series(df['QQQ'])
        curr_mode  = mode_s.iloc[-1]
        curr_dist  = float(dist_s.dropna().iloc[-1]) if not dist_s.dropna().empty else 0.0
        soxl_price = df['SOXL'].iloc[-1]
        prev_close = df['SOXL'].iloc[-2]
    else:
        curr_mode, curr_dist, soxl_price, prev_close = 'Safe', 0.0, 0.0, 0.0

    settings = load_settings()
    if 'auto_run_done' not in st.session_state:
        st.session_state['auto_run_done'] = False
    try:
        saved_start_date = datetime.strptime(settings.get("start_date", "2026-01-23"), "%Y-%m-%d").date()
        saved_init_cap   = float(settings.get("init_cap", 100000.0))
    except Exception:
        saved_start_date = datetime(2026, 1, 23).date()
        saved_init_cap   = 100000.0

    withdrawals_df = load_tax_withdrawals()
    if not offline_mode and ('holdings' not in st.session_state or not st.session_state['auto_run_done']):
        (h_auto, j_auto, eq_auto, log_auto, c_slot, c_day,
         c_wd, c_div, c_mode, c_dist) = auto_sync_engine(df, saved_start_date, saved_init_cap,
                                                          withdrawals_df=withdrawals_df)
        if h_auto is not None:
            old_h = load_csv(HOLDINGS_FILE, h_auto.columns)
            if len(h_auto) != len(old_h) or (not old_h.empty and str(h_auto.iloc[-1].values) != str(old_h.iloc[-1].values)):
                save_csv(h_auto, HOLDINGS_FILE); save_csv(j_auto, JOURNAL_FILE); save_csv(eq_auto, EQUITY_FILE)
            st.session_state.update({'holdings': h_auto, 'journal': j_auto, 'equity_history': eq_auto,
                'action_log': log_auto, 'current_slot_size': c_slot,
                'current_cycle': c_day, 'cum_withdrawn': c_wd, 'cum_dividends': c_div,
                'auto_run_done': True})

    if 'holdings' not in st.session_state:
        st.session_state['holdings'] = load_csv(HOLDINGS_FILE, ["매수일", "모드", "매수가", "수량", "목표가", "손절기한"])
    if 'journal' not in st.session_state:
        st.session_state['journal'] = load_csv(JOURNAL_FILE, ["날짜", "총자산", "수익금", "수익률"])
    if 'equity_history' not in st.session_state:
        st.session_state['equity_history'] = load_csv(EQUITY_FILE, ["날짜", "총자산"])
    if 'action_log' not in st.session_state:
        st.session_state['action_log'] = pd.DataFrame()

    curr_cum_wd = st.session_state.get('cum_withdrawn', 0.0)
    curr_cum_div = st.session_state.get('cum_dividends', 0.0)

    # ------------------------------------------------------------------ 실전 트레이딩
    with tab_trade:
        with st.sidebar:
            st.header("🤖 설정 및 초기화")
            auto_start_date = st.date_input("전략 시작일", value=saved_start_date)
            auto_init_cap   = st.number_input("시작 원금 ($)", value=saved_init_cap, step=100.0)
            if not offline_mode:
                if st.button("🔄 설정 변경 및 재동기화", type="primary"):
                    save_settings({"start_date": auto_start_date.strftime("%Y-%m-%d"), "init_cap": auto_init_cap})
                    st.session_state['auto_run_done'] = False
                    st.rerun()
            else:
                st.button("🚫 오프라인 (설정 변경 불가)", disabled=True)
            st.markdown("---")
            st.markdown("#### ⚙️ 떨사 200 파라미터")
            st.markdown(f"""
            | | ⚔️ 공격 | 🛡️ 방어 |
            |---|---|---|
            | 발동 | QQQ>200일선 | QQQ<200일선 |
            | 매수 | 전일 -0.1%↓ | 전일 -0.1%↓ |
            | 익절 | +2.5% | +0.01% |
            | 손절 | 8일 | 10일 |
            | 분할 | {BASE_SPLITS['Offense']}(+1) | {BASE_SPLITS['Safe']}(+1) |
            """)
            st.markdown(f"""
            **비대칭 복리** ({RESET_CYCLE}일 주기)
            · 상승복리 {int(UP_RATE*100)}% / 하락복리 {int(DN_RATE*100)}%
            """)
            st.markdown("---")
            if st.button("🗑️ 데이터 초기화"):
                save_csv(pd.DataFrame(columns=["매수일", "모드", "매수가", "수량", "목표가", "손절기한"]), HOLDINGS_FILE)
                save_csv(pd.DataFrame(columns=["날짜", "총자산", "수익금", "수익률"]), JOURNAL_FILE)
                save_csv(pd.DataFrame(columns=["날짜", "총자산"]), EQUITY_FILE)
                for key in ['holdings', 'journal', 'equity_history', 'action_log']:
                    st.session_state.pop(key, None)
                st.rerun()
            st.info(f"🔄 사이클: **{st.session_state.get('current_cycle', 1)}일차** / {RESET_CYCLE}일")

        r = PARAMS[curr_mode]
        slot_sz = st.session_state.get('current_slot_size', saved_init_cap / base_splits_for(curr_mode))
        today = get_now_kst().date()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("시장 레짐", "⚔️ 공격" if curr_mode == 'Offense' else "🛡️ 방어",
                  f"200MA 이격 {curr_dist:+.1f}%" if not offline_mode else "Offline", delta_color="off")
        c2.metric("SOXL 현재가", f"${soxl_price:.2f}" if not offline_mode else "Offline",
                  f"{((soxl_price-prev_close)/prev_close)*100:.2f}%" if (not offline_mode and prev_close > 0) else "-")
        c3.metric("1티어 슬롯", f"${slot_sz:,.0f}")
        c4.metric("분할 / 최대슬롯", f"{base_splits_for(curr_mode)}분할", f"최대 {max_slots_for(curr_mode)} (예비 포함)")
        c5.metric("매매 사이클", f"{st.session_state.get('current_cycle', 1)}일차")

        if not offline_mode:
            st.markdown(f"""
            <div class="st-card" style="border-left: 5px solid {'#d93025' if curr_mode=='Offense' else '#1a73e8'};">
                🧭 <strong>현재 레짐</strong> &nbsp;|&nbsp; {mode_badge(curr_mode)}
                &nbsp;&nbsp; QQQ 200일선 이격도 <strong>{curr_dist:+.2f}%</strong>
                &nbsp;→ 익절 <strong>{r['sell']}%</strong> · 손절 <strong>{r['time']}일</strong> · <strong>{base_splits_for(curr_mode)}분할</strong>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")
        st.subheader(f"📋 오늘의 주문 ({today.strftime('%Y-%m-%d')})")
        if offline_mode:
            st.warning("오프라인 모드에서는 주문을 생성할 수 없습니다.")
        else:
            df_h = st.session_state['holdings']
            sell_orders, buy_orders = [], []
            if not df_h.empty:
                df_h['손절기한'] = pd.to_datetime(df_h['손절기한']).dt.date
                for idx, row in df_h.iterrows():
                    if row['손절기한'] <= today:
                        sell_orders.append(f"티어{idx+1}: **{row['수량']}주** (시장가) — **MOC (기간만료)**")
                    else:
                        sell_orders.append(f"티어{idx+1}: **{row['수량']}주** (${row['목표가']:.2f}) — **LOC (익절)**")
            if soxl_price > 0:
                b_lim = soxl_price * (1 + r['buy'] / 100)
                b_qty = int(slot_sz / b_lim)
                buy_orders.append(f"신규: **{b_qty}주 (예상)** (${b_lim:.2f}) — **LOC** ({curr_mode})")
            if not sell_orders and not buy_orders:
                st.info("오늘 예정된 주문이 없습니다.")
            else:
                for o in sell_orders:
                    st.markdown(f'<div class="st-card" style="border-left:5px solid #d93025;"><span class="badge-sell">매도</span> {o}</div>', unsafe_allow_html=True)
                for o in buy_orders:
                    st.markdown(f'<div class="st-card" style="border-left:5px solid #1e8e3e;"><span class="badge-buy">매수</span> {o}</div>', unsafe_allow_html=True)

        st.markdown("---")
        _render_withdrawal_section(withdrawals_df, saved_init_cap, saved_start_date, today, offline_mode)

        st.markdown("---")
        st.subheader("📊 나의 티어 현황")
        df_h = st.session_state['holdings']
        if not df_h.empty:
            df_h['매수일'] = pd.to_datetime(df_h['매수일']).dt.date
            df_h.index = range(1, len(df_h) + 1); df_h.index.name = "티어"
            if not offline_mode:
                cy = ((soxl_price - df_h['매수가']) / df_h['매수가'] * 100)
                df_h['수익률'] = [f"{'🔺' if y > 0 else '🔻'} {y:.2f}%" for y in cy]
                df_h['상태'] = ["🚨 MOC 매도" if row['손절기한'] <= today else "🔵 LOC 대기" for _, row in df_h.iterrows()]
                tq = df_h['수량'].sum(); ti = (df_h['매수가'] * df_h['수량']).sum()
                avg = ti / tq if tq > 0 else 0; cv = tq * soxl_price; tp = cv - ti
                st.markdown("#### 📌 전체 계좌 요약")
                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("총 보유수량", f"{tq} 주"); sc2.metric("통합 평단가", f"${avg:,.2f}")
                sc3.metric("총 평가손익", f"${tp:,.2f}"); sc4.metric("평균 수익률", f"{(tp/ti*100) if ti>0 else 0:,.2f}%")
            edited_h = st.data_editor(df_h, num_rows="dynamic", use_container_width=True, key="h_edit",
                column_config={"수익률": st.column_config.TextColumn("수익률", disabled=True),
                               "매수가": st.column_config.NumberColumn(format="$%.2f"),
                               "목표가": st.column_config.NumberColumn(format="$%.2f"),
                               "상태": st.column_config.TextColumn(disabled=True)})
            if st.button("💾 티어 수정 저장 (GitHub)"):
                cols = ["매수일", "모드", "매수가", "수량", "목표가", "손절기한"]
                save_csv(edited_h[cols], HOLDINGS_FILE)
                st.session_state['holdings'] = edited_h[cols]
                st.success("저장되었습니다!"); st.rerun()
        else:
            st.info("현재 보유 중인 티어가 없습니다.")

        st.markdown("---")
        st.subheader("📝 매매 수익 기록장")
        df_j = st.session_state['journal']; df_eq = st.session_state['equity_history']; df_log = st.session_state['action_log']
        if not df_j.empty:
            tot_prof = df_j['수익금'].sum()
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("🏁 시작 원금", f"${saved_init_cap:,.0f}")
            m2.metric("💰 누적 수익금", f"${tot_prof:,.2f}")
            m3.metric("📈 총 수익률", f"{tot_prof/saved_init_cap*100:.1f}%")
            m4.metric("💸 누적 인출", f"${curr_cum_wd:,.0f}")
            m5.metric("💰 누적 배당", f"${curr_cum_div:,.2f}")
        else:
            st.info("아직 실현된 수익이 없습니다.")
        with st.expander(f"📜 전략 시작일({saved_start_date}) 이후 상세 매매 기록", expanded=False):
            if not df_log.empty:
                st.dataframe(df_log, use_container_width=True)
            else:
                st.caption("⚠️ 기록된 매매 내역이 없습니다.")
        st.markdown("### 📈 내 자산 성장 그래프")
        if not df_eq.empty:
            df_eq['날짜'] = pd.to_datetime(df_eq['날짜']); df_eq = df_eq.sort_values("날짜")
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(df_eq['날짜'], df_eq['총자산'], color='#1a73e8', linewidth=2)
            ax.fill_between(df_eq['날짜'], df_eq['총자산'], saved_init_cap,
                            where=(df_eq['총자산'] >= saved_init_cap), color='#1a73e8', alpha=0.1)
            ax.fill_between(df_eq['날짜'], df_eq['총자산'], saved_init_cap,
                            where=(df_eq['총자산'] < saved_init_cap), color='red', alpha=0.1)
            ax.axhline(y=saved_init_cap, color='gray', linestyle='--', alpha=0.5)
            ax.set_title("Total Equity Growth (Ddulsa 200)", fontweight='bold')
            ax.grid(True, linestyle='--', alpha=0.3)
            ax.yaxis.set_major_formatter(mtick.StrMethodFormatter('${x:,.0f}'))
            st.pyplot(fig)
        else:
            st.info("그래프 데이터가 없습니다.")

    # ------------------------------------------------------------------ 백테스트
    with tab_backtest:
        st.header("🧪 백테스트 성과분석 (떨사 200)")
        if offline_mode:
            st.warning("오프라인 모드에서는 백테스트를 실행할 수 없습니다.")
        else:
            bt_init_cap = st.number_input("백테스트 초기 자본 ($)", value=10000.0, step=1000.0)
            bc1, bc2 = st.columns(2)
            start_d = bc1.date_input("검증 시작일", value=datetime(2010, 1, 1), min_value=datetime(2000, 1, 1))
            end_d   = bc2.date_input("검증 종료일", value=today, min_value=datetime(2000, 1, 1))
            with st.expander("⚙️ 수수료 & 양도세 옵션 (현실 반영)", expanded=False):
                opt1, opt2 = st.columns(2)
                with opt1:
                    inc_fees = st.checkbox("거래 수수료 적용", value=False,
                                           help="매수 0.015% / 매도 0.01706%(commission+SEC fee)")
                    if inc_fees:
                        st.caption("매수 0.015% / 매도 0.01706%")
                with opt2:
                    inc_tax = st.checkbox("양도세 적용 (시뮬)", value=False)
                    if inc_tax:
                        krw_rate = st.number_input("USD/KRW 환율", value=1400, min_value=800, max_value=2000, step=10)
                        ded_krw  = st.number_input("연 공제 한도 (KRW)", value=2_500_000, min_value=0, step=100_000)
                        ded_usd  = ded_krw / krw_rate
                        st.caption(f"공제 USD 환산: ${ded_usd:,.0f} | 세율 22%")
                        tax_strategy = st.selectbox("양도세 인출 전략", options=['A', 'B'], index=0,
                            format_func=lambda x: {'A': 'A: 5월 일괄인출 (default)', 'B': '🎨 B: 커스텀 (직접 월 선택)'}[x])
                        custom_schedule = None
                        if tax_strategy == 'B':
                            st.markdown("##### 🎨 인출 월 선택 (선택 개수만큼 균등 분할)")
                            month_defs = [('Dec', '전년 12월', 12, -1), ('Jan', '1월', 1, 0), ('Feb', '2월', 2, 0),
                                          ('Mar', '3월', 3, 0), ('Apr', '4월', 4, 0), ('May', '5월', 5, 0),
                                          ('Jun', '6월', 6, 0), ('Jul', '7월', 7, 0), ('Aug', '8월', 8, 0)]
                            cb_cols = st.columns(9); selected_months = []
                            for i, (key, lbl, mnum, yoff) in enumerate(month_defs):
                                if cb_cols[i].checkbox(lbl, value=False, key=f"tax_m_{key}"):
                                    selected_months.append((key, lbl, mnum, yoff))
                            if not selected_months:
                                st.warning("⚠️ 최소 1개 월 선택. 없으면 A(5월)로 fallback."); tax_strategy = 'A'
                            else:
                                frac_each = 1.0 / len(selected_months); custom_schedule = []
                                for key, lbl, mnum, yoff in selected_months:
                                    if yoff == -1:
                                        custom_schedule.append((frac_each, (12, 1), (12, 31), -1))
                                    else:
                                        custom_schedule.append((frac_each, (mnum, 1), (8, 31)))
                                st.info(f"✅ 선택: {', '.join(m[1] for m in selected_months)} "
                                        f"({len(selected_months)}개 × {frac_each*100:.1f}%)")
                    else:
                        ded_usd, krw_rate, tax_strategy, custom_schedule = 1786.0, 1400, 'A', None
            if st.button("🚀 분석 실행"):
                with st.spinner("분석 중..."):
                    res, metrics, df_yearly, df_debug = run_backtest_fixed(
                        df, start_d, end_d, bt_init_cap, include_fees=inc_fees, include_tax=inc_tax,
                        tax_deduction_usd=ded_usd, tax_rate=0.22, tax_strategy=tax_strategy,
                        custom_schedule=custom_schedule if inc_tax else None)
                    if res is not None:
                        final = res['Equity'].iloc[-1]; ret = final / bt_init_cap - 1
                        days = (res.index[-1] - res.index[0]).days
                        cagr = (1 + ret) ** (365 / days) - 1 if days > 0 else 0
                        res['Peak'] = res['Equity'].cummax()
                        res['Drawdown'] = (res['Equity'] - res['Peak']) / res['Peak']
                        mdd = res['Drawdown'].min(); calmar = cagr / abs(mdd) if mdd != 0 else 0
                        m1, m2, m3, m4, m5, m6 = st.columns(6)
                        m1.metric("최종 수익금", f"${final:,.0f}", f"{ret*100:,.1f}%")
                        m2.metric("CAGR", f"{cagr*100:.2f}%")
                        m3.metric("MDD", f"{mdd*100:.2f}%", delta_color="inverse")
                        m4.metric("Calmar", f"{calmar:.2f}")
                        m5.metric("Sortino", f"{metrics['sortino']:.2f}")
                        m6.metric("Profit Factor", f"{metrics['profit_factor']:.2f}")
                        st.markdown("#### 💰 부수 효과")
                        dc1, dc2 = st.columns(2)
                        dc1.metric("누적 배당 (재투자)", f"${metrics.get('total_dividends', 0):,.0f}")
                        dc2.metric("배당 비중", f"{(metrics.get('total_dividends',0)/final*100):.2f}%" if final > 0 else "─")
                        if metrics.get('include_fees') or metrics.get('include_tax'):
                            slabel = {'A': 'A: 5월 일괄인출', 'B': '🎨 B: 커스텀'}.get(metrics.get('tax_strategy', 'A'), 'A')
                            st.markdown("#### 💰 수수료 & 양도세 (시뮬)" + (f" — 인출 전략: **{slabel}**" if metrics.get('include_tax') else ""))
                            tc1, tc2, tc3, tc4 = st.columns(4)
                            tc1.metric("누적 수수료", f"${metrics['total_fees']:,.0f}",
                                       f"매수 ${metrics['total_buy_fees']:,.0f} / 매도 ${metrics['total_sell_fees']:,.0f}" if metrics.get('include_fees') else "─", delta_color="off")
                            tc2.metric("누적 양도세", f"${metrics['total_tax_paid']:,.0f}" if metrics.get('include_tax') else "─",
                                       f"22% / 연 ${ded_usd:,.0f} 공제" if metrics.get('include_tax') else "(미적용)", delta_color="off")
                            tc3.metric("미정산 양도세", f"${metrics['tax_pending_end']:,.0f}" if metrics.get('include_tax') else "─", delta_color="off")
                            after_final = final - metrics['tax_pending_end']
                            after_cagr = (after_final / bt_init_cap) ** (365 / days) - 1 if days > 0 else 0
                            tc4.metric("세후 추정 CAGR", f"{after_cagr*100:.2f}%", f"{(after_cagr-cagr)*100:+.2f}pp", delta_color="off")
                        st.markdown("#### 📊 통합 성과 차트")
                        fig, ax1 = plt.subplots(figsize=(12, 6))
                        ax1.set_xlabel('Date'); ax1.set_ylabel('Total Equity ($)', color='tab:blue', fontweight='bold')
                        ax1.plot(res.index, res['Equity'], color='tab:blue', linewidth=1.5)
                        ax1.tick_params(axis='y', labelcolor='tab:blue')
                        ax1.yaxis.set_major_formatter(mtick.StrMethodFormatter('${x:,.0f}'))
                        ax1.grid(True, linestyle='--', alpha=0.3)
                        ax2 = ax1.twinx(); ax2.set_ylabel('Drawdown (%)', color='tab:red', fontweight='bold')
                        ax2.fill_between(res.index, res['Drawdown'] * 100, 0, color='tab:red', alpha=0.2)
                        ax2.tick_params(axis='y', labelcolor='tab:red'); ax2.set_ylim(-100, 5)
                        ax2.yaxis.set_major_formatter(mtick.PercentFormatter())
                        plt.title("Ddulsa 200 — Performance vs Risk", fontweight='bold'); plt.tight_layout()
                        st.pyplot(fig)
                        st.markdown("#### 📅 연도별 성과표")
                        dyf = df_yearly.copy()
                        dyf['수익률'] = dyf['수익률'].apply(lambda x: f"{x*100:.1f}%")
                        dyf['MDD'] = dyf['MDD'].apply(lambda x: f"{x*100:.1f}%")
                        dyf['기말자산'] = dyf['기말자산'].apply(lambda x: f"${x:,.0f}")
                        dyf['배당'] = dyf['배당'].apply(lambda x: f"${x:,.0f}")
                        if metrics.get('include_fees'):
                            dyf['수수료'] = dyf['수수료'].apply(lambda x: f"${x:,.0f}")
                        else:
                            dyf = dyf.drop(columns=['수수료'], errors='ignore')
                        if metrics.get('include_tax'):
                            dyf['양도세'] = dyf['양도세'].apply(lambda x: f"${x:,.0f}")
                            dyf['실현손익'] = dyf['실현손익'].apply(lambda x: f"${x:,.0f}")
                        else:
                            dyf = dyf.drop(columns=['양도세', '실현손익'], errors='ignore')
                        st.dataframe(dyf.T, use_container_width=True)
                        if df_debug is not None and not df_debug.empty:
                            n_buy = (df_debug['Action'] == '🟢 매수').sum()
                            n_sell = (df_debug['Action'] == '🔴 매도').sum()
                            st.markdown("#### 📋 전체 매매 로그")
                            st.caption(f"총 매매 건수: **{len(df_debug):,}건** (매수 {n_buy:,} / 매도 {n_sell:,})")
                            st.caption("🔎 우측에 QQQ종가 · QQQ_200MA · 이격% · op_base(운용원금) 컬럼 포함. CSV로 내보내 분석하세요.")
                            st.dataframe(df_debug.sort_values('날짜', ascending=False).reset_index(drop=True),
                                         use_container_width=True, height=500, hide_index=True)
                        else:
                            st.info("매매 발생 없음")
                    else:
                        st.error("데이터 부족")

    # ------------------------------------------------------------------ 전략 로직
    with tab_logic:
        st.header("📚 떨사 200 (Ddulsa 200) 전략 매뉴얼")
        st.caption("현재 버전: v1.0")
        st.subheader("1. 핵심 전략 (Core Strategy)")
        st.markdown("""
        > **한 줄 요약:** "QQQ 200일선으로 장세를 읽고, 상승장엔 공격적으로·하락장엔 방어적으로 SOXL을 분할매수한다."
        - **대상 종목:** SOXL (반도체 3배 레버리지 ETF)
        - **레짐 지표:** QQQ(나스닥100) 200일 이동평균선 — 매매는 SOXL, 판단은 QQQ
        - **성격:** 추세추종형 레짐 스위치 × 분할매수 평균회귀 하이브리드
        """)
        st.markdown("**① 레짐 스위치 (QQQ 200일선)**")
        st.markdown(f"""
        | 레짐 | 발동 조건 | 분할 | 매수 타점 | 익절 | 손절 |
        |---|---|---|---|---|---|
        | ⚔️ 공격 (Offense) | QQQ > 200일선 | {BASE_SPLITS['Offense']}분할(+예비1) | 전일종가 -0.1%↓ (LOC) | +2.5% (LOC) | 8거래일 (MOC) |
        | 🛡️ 방어 (Safe) | QQQ < 200일선 | {BASE_SPLITS['Safe']}분할(+예비1) | 전일종가 -0.1%↓ (LOC) | +0.01% (LOC) | 10거래일 (MOC) |
        """)
        st.markdown("**② 티어 관리 & 예비티어**")
        st.markdown(f"""
        - 매일 조건 충족 시 1티어씩 매수. 각 티어는 **진입 당시 레짐의 규칙(익절·손절)**을 그대로 유지하며 개별 관리.
        - 6분할(공격)/4분할(방어) 소진 후에도 하락 지속 시 **예비티어 1개**까지 추가 매수 (최대 {MAX_SLOTS['Offense']}/{MAX_SLOTS['Safe']}티어).
        - 익절 목표 도달(LOC) 또는 보유기간 만료(MOC) 시 청산.
        """)
        st.markdown("**③ 비대칭 복리 (동파 방식)**")
        st.markdown(f"""
        - **{RESET_CYCLE}거래일**마다 운용원금(op_base) 재산정 → 1티어 크기 = op_base ÷ 분할수.
        - 사이클 실현손익이 **+면 {int(UP_RATE*100)}%만 원금에 재투자**(나머지는 예비비), **−면 {int(DN_RATE*100)}%만 원금에서 차감**(하락 시 사이징 급감 방지).
        - 자산 마킹은 매일 **현금 + 보유평가액**(증권사 기준 정확 마킹).
        """)
        st.markdown("---")
        st.subheader("2. 설계 근거 (요약)")
        st.markdown("""
        - **왜 200일선 + 공격/방어인가:** SOXL은 우상향 추세가 강한 3배 레버리지 자산이라, "천장에서 방어·바닥에서 공격"하는 평균회귀식 스위치(RSI·이격도)는 역효과였음. 반대로 **추세가 살아있을 때(200일선 위) 공격, 무너질 때(아래) 방어**하는 추세추종 스위치가 위험대비수익(Calmar)을 최적화.
        - **왜 이 파라미터인가:** 2010~2026 그리드 서치 + 워크포워드 검증 결과, 공격 손절 8일·익절 +2.5%·균등분할이 견고. 손절일 단축이 낙폭 억제의 최대 레버였음.
        - **주의:** SOXL 단일 자산 백테스트 기반. 레버리지 ETF는 변동성 손실 위험이 있으며 미래 성과를 보장하지 않음. 실행 전 다른 3배 ETF 교차검증 권장.
        """)
        st.markdown("---")
        st.subheader("3. 업데이트 이력")
        with st.expander("v1.0 — 최초 릴리스", expanded=True):
            st.markdown("""
            - 동파법 마스터 앱 구조(3탭·GitHub/시트 연동·수수료/양도세·배당 재투자·재기자본 인출)를 이식.
            - 핵심 로직을 떨사 200으로 교체: QQQ 200일선 레짐 스위치, 공격/방어 균등분할, 비대칭 복리(80/30, 12일).
            - 동파 고유의 QS_strength·Loss-Streak 사이징 가드는 떨사 200에 없어 제거.
            """)

# ============================================================================
#  양도세 / 재기자본 인출 섹션 (실전 트레이딩 탭 내부)
# ============================================================================
def _render_withdrawal_section(withdrawals_df, saved_init_cap, saved_start_date, today, offline_mode):
    df_j_for_tax = st.session_state.get('journal', pd.DataFrame())
    yearly_realized_for_tax = {}
    if not df_j_for_tax.empty and '날짜' in df_j_for_tax.columns and '수익금' in df_j_for_tax.columns:
        try:
            t = df_j_for_tax.copy()
            t['_year'] = pd.to_datetime(t['날짜']).dt.year
            t['_pnl'] = pd.to_numeric(t['수익금'], errors='coerce').fillna(0)
            yearly_realized_for_tax = t.groupby('_year')['_pnl'].sum().to_dict()
        except Exception:
            yearly_realized_for_tax = {}
    DEFAULT_TAX_DEDUCTION_USD = 2_500_000 / 1400
    DEFAULT_TAX_RATE = 0.22
    last_yr = today.year - 1
    last_yr_realized = float(yearly_realized_for_tax.get(last_yr, 0.0))
    expected_tax = max(0.0, last_yr_realized - DEFAULT_TAX_DEDUCTION_USD) * DEFAULT_TAX_RATE
    wd_df_pre = withdrawals_df.copy() if withdrawals_df is not None else pd.DataFrame(columns=["날짜", "금액", "메모"])
    already_paid = 0.0
    if not wd_df_pre.empty:
        try:
            wd_df_pre['_yr'] = pd.to_datetime(wd_df_pre['날짜']).dt.year
            already_paid = float(wd_df_pre[wd_df_pre['_yr'] == today.year]['금액'].sum())
        except Exception:
            pass
    remaining_tax = max(0.0, expected_tax - already_paid)
    show_alert = (5 <= today.month <= 8) and remaining_tax > 0

    with st.expander(("🔴 [양도세 인출 시기] " if show_alert else "") + "💸 양도세 인출 기록 관리 (시트 → 봇 잔고 반영)",
                     expanded=show_alert):
        if show_alert:
            st.error(f"🔴 {last_yr}년분 양도세 인출 시기 — 예상 세액 ${expected_tax:,.0f} / 이미 인출 ${already_paid:,.0f} "
                     f"→ 남은 인출 ${remaining_tax:,.0f} (신고 마감 5/31, 분납 8/31)")
        wb_ok = get_gspread_workbook() is not None
        if not wb_ok:
            st.warning("⚠️ GCP_CREDENTIALS 미설정 또는 시트 권한 부족 — 인출 기록 저장 불가.")
        wd_df = withdrawals_df.copy() if withdrawals_df is not None else pd.DataFrame(columns=["날짜", "금액", "메모"])
        if not wd_df.empty:
            _sm = wd_df["메모"].apply(is_seed_memo)
            wd_df_seed, wd_df_tax = wd_df[_sm].copy(), wd_df[~_sm].copy()
        else:
            wd_df_seed = pd.DataFrame(columns=["날짜", "금액", "메모"])
            wd_df_tax = pd.DataFrame(columns=["날짜", "금액", "메모"])
        display_df = wd_df_tax.copy() if not wd_df_tax.empty else pd.DataFrame(
            {"날짜": pd.Series(dtype="datetime64[ns]"), "금액": pd.Series(dtype="float64"), "메모": pd.Series(dtype="object")})
        edited_wd = st.data_editor(display_df, num_rows="dynamic", use_container_width=True, key="wd_editor",
            column_config={"날짜": st.column_config.DateColumn("날짜", format="YYYY-MM-DD", required=True),
                           "금액": st.column_config.NumberColumn("금액 ($)", format="$%.2f", min_value=0.0, required=True),
                           "메모": st.column_config.TextColumn("메모")}, disabled=not wb_ok)
        if st.button("💾 양도세 인출 저장 (시트 → 잔고 재계산)", disabled=not wb_ok):
            merged = pd.concat([edited_wd, wd_df_seed], ignore_index=True)
            if save_tax_withdrawals(merged):
                st.session_state['auto_run_done'] = False
                try:
                    st.cache_resource.clear()
                except Exception:
                    pass
                st.success("저장 완료. 재실행합니다…"); st.rerun()

    # 재기자본 인출
    _wd_all = withdrawals_df.copy() if withdrawals_df is not None else pd.DataFrame(columns=["날짜", "금액", "메모"])
    if not _wd_all.empty:
        _sm = _wd_all["메모"].apply(is_seed_memo)
        seed_rows, tax_rows = _wd_all[_sm].copy(), _wd_all[~_sm].copy()
    else:
        seed_rows = pd.DataFrame(columns=["날짜", "금액", "메모"])
        tax_rows = pd.DataFrame(columns=["날짜", "금액", "메모"])
    _df_eq_s = st.session_state.get('equity_history', pd.DataFrame())
    try:
        cur_total_asset = float(pd.to_numeric(_df_eq_s["총자산"], errors="coerce").dropna().iloc[-1]) if not _df_eq_s.empty else float(saved_init_cap)
    except Exception:
        cur_total_asset = float(saved_init_cap)
    seed_vault = float(pd.to_numeric(seed_rows["금액"], errors="coerce").sum()) if not seed_rows.empty else 0.0
    if 'seed_anchor' not in st.session_state:
        st.session_state['seed_anchor'] = cur_total_asset
    seed_ref = seed_reference(seed_rows, st.session_state['seed_anchor'])
    seed_target = SEED_MULT * seed_ref
    seed_trig = cur_total_asset >= seed_target
    seed_amt = SEED_PCT * cur_total_asset
    seed_new_ref = cur_total_asset - seed_amt
    seed_gap = max(0.0, seed_target - cur_total_asset)
    with st.expander(("🟢 [2배 도달 — 7% 인출 신호] " if seed_trig else "") + "🌱 재기자본 인출 관리 (자산 2배마다 7% → 안전금고)",
                     expanded=seed_trig):
        if seed_trig:
            st.success(f"🟢 자산 2배 도달 (${cur_total_asset:,.0f} ≥ ${seed_target:,.0f}) — 권장 인출 7% = **${seed_amt:,.0f}** "
                       f"→ SGOV 등 초단기국채로 이체 후 아래에 기록. 새 기준점 ${seed_new_ref:,.0f}")
        else:
            st.info(f"📈 현재 ${cur_total_asset:,.0f} / 다음 인출 기준(2배) ${seed_target:,.0f} — ${seed_gap:,.0f} 남음")
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("🏦 재기자본 금고", f"${seed_vault:,.0f}", f"{len(seed_rows)}회" if not seed_rows.empty else "0회")
        sc2.metric("📈 현재 총자산", f"${cur_total_asset:,.0f}")
        sc3.metric("🎯 다음 2배 기준", f"${seed_target:,.0f}")
        st.number_input("시작 기준점 ($)", step=100.0, key="seed_anchor")
        wb_ok2 = get_gspread_workbook() is not None
        if not wb_ok2:
            st.warning("⚠️ 시트 권한 부족으로 저장 불가.")
        with st.form("seed_add_form"):
            st.markdown("**재기자본 인출 기록 추가**")
            sf1, sf2 = st.columns(2)
            s_date = sf1.date_input("인출일", value=today)
            s_amt = sf2.number_input("인출 금액 ($)", value=round(seed_amt, 2), step=10.0, min_value=0.0)
            s_memo = st.text_input("메모 (자동 태그)", value=f"재기씨앗 7% | ref={seed_new_ref:.0f}")
            if st.form_submit_button("🌱 재기자본 인출 기록 & 저장", disabled=not wb_ok2):
                if not is_seed_memo(s_memo):
                    s_memo = "재기씨앗 | " + s_memo
                new_row = pd.DataFrame([{"날짜": pd.to_datetime(s_date), "금액": float(s_amt), "메모": s_memo}])
                merged = pd.concat([tax_rows, seed_rows, new_row], ignore_index=True)
                if save_tax_withdrawals(merged):
                    st.session_state['auto_run_done'] = False
                    try:
                        st.cache_resource.clear()
                    except Exception:
                        pass
                    st.success("저장 완료. 재실행합니다…"); st.rerun()
        st.caption("⚠️ 금고 원칙: SGOV 등 초단기국채/현금으로만 보관. 절대 SOXL로 되돌리지 않음.")

if __name__ == "__main__":
    main()
