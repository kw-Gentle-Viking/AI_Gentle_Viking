"""
validate_raw.py
===============
피처 생성 전 raw 테이블 검증
- 각 테이블의 필수 컬럼 NULL/NaN/0 체크
- 날짜별 종목수 이상치 체크
- 조인 키 일치 여부 체크

실행:
    python ~/validate_raw.py
"""

import os
import psycopg2
import pandas as pd
from datetime import date, timedelta

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

TODAY     = date.today()
LOOKBACK  = 10  # 최근 N거래일 체크
EXPECTED_TICKERS  = 349
EXPECTED_SECTORS  = 25

conn = psycopg2.connect(**DB_CONFIG)
cur  = conn.cursor()

errors   = []
warnings = []

def ok(msg):    print(f"  ✅ {msg}")
def warn(msg):  print(f"  ⚠️  {msg}"); warnings.append(msg)
def err(msg):   print(f"  ❌ {msg}"); errors.append(msg)


# ============================================================
print("\n" + "="*60)
print("1. price_daily 검증")
print("="*60)

cur.execute("""
    SELECT trade_date,
           COUNT(*) AS total,
           COUNT(close_price) FILTER (WHERE close_price > 0) AS valid_close,
           COUNT(turnover) FILTER (WHERE turnover > 0) AS valid_turnover,
           COUNT(shares_outstanding) FILTER (WHERE shares_outstanding > 0) AS valid_shares,
           COUNT(*) FILTER (WHERE close_price IS NULL OR close_price::text = 'NaN' OR close_price <= 0) AS bad_close
    FROM price_daily
    WHERE trade_date >= %s
    GROUP BY trade_date ORDER BY trade_date DESC
""", (TODAY - timedelta(days=LOOKBACK*2),))
rows = cur.fetchall()

print(f"  {'날짜':<12} {'종목':>5} {'종가OK':>6} {'거래대금':>8} {'상장주식수':>10} {'비정상종가':>10}")
for r in rows:
    line = f"  {str(r[0]):<12} {r[1]:>5} {r[2]:>6} {r[3]:>8} {r[4]:>10} {r[5]:>10}"
    print(line)
    if r[1] < EXPECTED_TICKERS - 5:
        warn(f"price_daily {r[0]}: 종목수 {r[1]}개 (예상 {EXPECTED_TICKERS})")
    if r[4] == 0:
        warn(f"price_daily {r[0]}: shares_outstanding 전부 0")
    if r[5] > 0:
        err(f"price_daily {r[0]}: 비정상 종가 {r[5]}개")

# ============================================================
print("\n" + "="*60)
print("2. investor_flow_daily 검증")
print("="*60)

cur.execute("""
    SELECT trade_date,
           COUNT(*) AS total,
           COUNT(*) FILTER (WHERE individual_net_amt = 0 AND foreign_net_amt = 0
                            AND inst_net_amt = 0) AS all_zero,
           COUNT(market_cap) FILTER (WHERE market_cap > 0) AS valid_mktcap,
           EXTRACT(DOW FROM trade_date) AS dow
    FROM investor_flow_daily
    WHERE trade_date >= %s
    GROUP BY trade_date ORDER BY trade_date DESC
    LIMIT 15
""", (TODAY - timedelta(days=LOOKBACK*3),))
rows = cur.fetchall()

print(f"  {'날짜':<12} {'종목':>5} {'전부0':>6} {'시총OK':>6} {'요일':>4}")
for r in rows:
    dow_name = ['일','월','화','수','목','금','토'][int(r[4])]  # PostgreSQL DOW: 0=일
    line = f"  {str(r[0]):<12} {r[1]:>5} {r[2]:>6} {r[3]:>6} {dow_name:>4}"
    print(line)
    if int(r[4]) in (0, 6):
        err(f"investor_flow_daily {r[0]}: 주말({dow_name}) 데이터 존재")
    if r[2] > 0:
        warn(f"investor_flow_daily {r[0]}: 전부0 종목 {r[2]}개 (장 시작 전 수집 의심)")
    if r[3] == 0:
        warn(f"investor_flow_daily {r[0]}: market_cap 없음 (prop_* 계산 불가)")

# ============================================================
print("\n" + "="*60)
print("2-1. 주말 데이터 존재 여부 검증")
print("="*60)

WEEKDAY_TABLES = [
    'price_daily', 'investor_flow_daily', 'daily_valuation',
    'sector_daily_ohlcv', 'market_index_daily'
]
for t in WEEKDAY_TABLES:
    cur.execute(f"SELECT COUNT(*) FROM {t} WHERE EXTRACT(DOW FROM trade_date) IN (0, 6)")
    cnt = cur.fetchone()[0]
    if cnt > 0:
        err(f"{t}: 주말 데이터 {cnt}행 존재 → 삭제 필요")
    else:
        ok(f"{t}: 주말 데이터 없음")

# ============================================================
print("\n" + "="*60)
print("3. daily_valuation 검증")
print("="*60)

cur.execute("""
    SELECT trade_date,
           COUNT(*) AS total,
           COUNT(per) FILTER (WHERE per > 0) AS valid_per,
           COUNT(pbr) FILTER (WHERE pbr > 0) AS valid_pbr,
           COUNT(market_cap) FILTER (WHERE market_cap > 0) AS valid_mktcap,
           COUNT(*) FILTER (WHERE per::text = 'NaN' OR pbr::text = 'NaN') AS nan_cnt
    FROM daily_valuation
    WHERE trade_date >= %s
    GROUP BY trade_date ORDER BY trade_date DESC
    LIMIT 10
""", (TODAY - timedelta(days=LOOKBACK*2),))
rows = cur.fetchall()

print(f"  {'날짜':<12} {'종목':>5} {'PER':>5} {'PBR':>5} {'시총':>5} {'NaN':>5}")
for r in rows:
    line = f"  {str(r[0]):<12} {r[1]:>5} {r[2]:>5} {r[3]:>5} {r[4]:>5} {r[5]:>5}"
    print(line)
    if r[5] > 0:
        err(f"daily_valuation {r[0]}: NaN {r[5]}개")
    if r[4] == 0:
        warn(f"daily_valuation {r[0]}: market_cap 없음")

# ============================================================
print("\n" + "="*60)
print("4. market_global 검증")
print("="*60)

cur.execute("""
    SELECT trade_date,
           COUNT(*) FILTER (WHERE snp500_close IS NULL OR snp500_close::text = 'NaN' OR snp500_close <= 0) AS bad_snp,
           COUNT(*) FILTER (WHERE usd_krw IS NULL OR usd_krw::text = 'NaN' OR usd_krw <= 0) AS bad_usdkrw,
           COUNT(*) FILTER (WHERE vix IS NULL OR vix::text = 'NaN' OR vix <= 0) AS bad_vix,
           COUNT(*) FILTER (WHERE fed_rate IS NULL OR fed_rate::text = 'NaN') AS bad_fed,
           COUNT(*) FILTER (WHERE kr_base_rate IS NULL OR kr_base_rate::text = 'NaN') AS bad_kr,
           EXTRACT(DOW FROM trade_date) AS dow
    FROM market_global
    WHERE trade_date >= %s
    GROUP BY trade_date ORDER BY trade_date DESC
    LIMIT 10
""", (TODAY - timedelta(days=LOOKBACK*2),))
rows = cur.fetchall()

print(f"  {'날짜':<12} {'S&P':>5} {'환율':>5} {'VIX':>5} {'연준':>5} {'한국':>5} {'요일':>4}")
for r in rows:
    dow_name = ['월','화','수','목','금','토','일'][int(r[6])]
    line = f"  {str(r[0]):<12} {r[1]:>5} {r[2]:>5} {r[3]:>5} {r[4]:>5} {r[5]:>5} {dow_name:>4}"
    print(line)
    if any(r[i] > 0 for i in range(1, 6)):
        err(f"market_global {r[0]}: 비정상값 존재 S&P={r[1]} 환율={r[2]} VIX={r[3]} 연준={r[4]} 한국={r[5]}")

# ============================================================
print("\n" + "="*60)
print("5. sector_daily_ohlcv 검증")
print("="*60)

# 추론용 sector_code 목록
cur.execute("SELECT DISTINCT sector_code FROM sector_daily_ohlcv ORDER BY sector_code")
sector_codes = [r[0] for r in cur.fetchall()]
print(f"  sector_daily_ohlcv 코드: {sector_codes}")

cur.execute("""
    SELECT trade_date,
           COUNT(DISTINCT sector_code) AS sector_cnt,
           COUNT(*) FILTER (WHERE close IS NULL OR close::text = 'NaN' OR close <= 0) AS bad_close,
           COUNT(*) FILTER (WHERE volume IS NULL OR volume = 0) AS zero_vol
    FROM sector_daily_ohlcv
    WHERE trade_date >= %s
    GROUP BY trade_date ORDER BY trade_date DESC
    LIMIT 10
""", (TODAY - timedelta(days=LOOKBACK*2),))
rows = cur.fetchall()

print(f"  {'날짜':<12} {'섹터수':>6} {'비정상종가':>10} {'거래량0':>8}")
for r in rows:
    line = f"  {str(r[0]):<12} {r[1]:>6} {r[2]:>10} {r[3]:>8}"
    print(line)
    if r[1] != EXPECTED_SECTORS:
        warn(f"sector_daily_ohlcv {r[0]}: 섹터수 {r[1]} (예상 {EXPECTED_SECTORS})")
    if r[2] > 0:
        err(f"sector_daily_ohlcv {r[0]}: 비정상 종가 {r[2]}개")

# ============================================================
print("\n" + "="*60)
print("6. 조인 키 일치 여부 검증")
print("="*60)

# ticker_metadata의 sector_id → sector_code 변환 후 sector_daily_ohlcv와 비교
SECTOR_ID_TO_CODE = {
    0: "0005", 1: "0006", 2: "0007", 3: "0008",
    4: "0009", 5: "0010", 6: "0011", 7: "0012",
    8: "0013", 9: "0014", 10: "0015", 11: "0016",
    12: "0017", 13: "0018", 14: "0019", 15: "0020",
    16: "0021", 17: "0024", 18: "0025", 19: "0026",
}

cur.execute("SELECT DISTINCT sector_id FROM ticker_metadata WHERE sector_id IS NOT NULL")
meta_sector_ids = set(r[0] for r in cur.fetchall())
mapped_codes = set(SECTOR_ID_TO_CODE.get(sid) for sid in meta_sector_ids if sid in SECTOR_ID_TO_CODE)
unmapped_ids = [sid for sid in meta_sector_ids if sid not in SECTOR_ID_TO_CODE]
missing_codes = mapped_codes - set(sector_codes)

if unmapped_ids:
    err(f"SECTOR_ID_TO_CODE 매핑 없는 sector_id: {unmapped_ids}")
else:
    ok("모든 sector_id가 SECTOR_ID_TO_CODE에 매핑됨")

if missing_codes:
    err(f"sector_daily_ohlcv에 없는 sector_code: {missing_codes}")
else:
    ok("매핑된 sector_code 모두 sector_daily_ohlcv에 존재")

# ticker_metadata vs investor_flow_daily 종목 비교
cur.execute("SELECT DISTINCT ticker FROM ticker_metadata")
meta_tickers = set(r[0] for r in cur.fetchall())

cur.execute("SELECT DISTINCT ticker FROM investor_flow_daily WHERE trade_date = %s", (TODAY,))
flow_tickers = set(r[0] for r in cur.fetchall())

missing_flow = meta_tickers - flow_tickers
extra_flow   = flow_tickers - meta_tickers
if missing_flow:
    warn(f"investor_flow_daily 오늘 누락 종목: {len(missing_flow)}개 {list(missing_flow)[:5]}")
else:
    ok("investor_flow_daily 오늘 종목 완전 일치")
if extra_flow:
    warn(f"investor_flow_daily 오늘 ticker_metadata에 없는 종목: {extra_flow}")

# ============================================================
print("\n" + "="*60)
print("7. market_index_daily 검증")
print("="*60)

cur.execute("""
    SELECT index_code, MAX(trade_date), COUNT(*),
           COUNT(*) FILTER (WHERE close_price IS NULL OR close_price <= 0) AS bad
    FROM market_index_daily
    GROUP BY index_code ORDER BY index_code
""")
for r in cur.fetchall():
    line = f"  {r[0]:10s}: 최신={r[1]} / {r[2]}행 / 비정상={r[3]}"
    print(line)
    if r[3] > 0:
        err(f"market_index_daily {r[0]}: 비정상값 {r[3]}개")
    if str(r[1]) != str(TODAY) and TODAY.weekday() < 5:
        warn(f"market_index_daily {r[0]}: 오늘({TODAY}) 데이터 없음")

# ============================================================
print("\n" + "="*60)
print("최종 요약")
print("="*60)
print(f"  ❌ 오류: {len(errors)}개")
for e in errors:
    print(f"     - {e}")
print(f"  ⚠️  경고: {len(warnings)}개")
for w in warnings:
    print(f"     - {w}")

if not errors and not warnings:
    print("  ✅ 모든 검증 통과!")

conn.close()