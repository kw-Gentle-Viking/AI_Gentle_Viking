"""
validate_features.py
====================
피처 생성 후 inference_features / realtime_features 검증
- NULL / NaN / 0 체크
- 컬럼별 기대 범위 체크
- 날짜별 종목수 일치 여부 체크
- 조인 실패 여부 체크

실행:
    python ~/validate_features.py
"""

import os
import psycopg2
import numpy as np
from datetime import date, timedelta

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

TODAY    = date.today()
EXPECTED = 349

conn = psycopg2.connect(**DB_CONFIG)
cur  = conn.cursor()

errors   = []
warnings = []

def ok(msg):    print(f"  ✅ {msg}")
def warn(msg):  print(f"  ⚠️  {msg}"); warnings.append(msg)
def err(msg):   print(f"  ❌ {msg}"); errors.append(msg)


# ============================================================
print("\n" + "="*60)
print("inference_features 검증")
print("="*60)

# 1. 기본 현황
cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date),
           COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT trade_date)
    FROM inference_features
""")
r = cur.fetchone()
print(f"  기간: {r[0]} ~ {r[1]} / {r[2]:,}행 / {r[3]}종목 / {r[4]}거래일")

cur.execute("SELECT COUNT(*) FROM inference_features WHERE trade_date = %s", (TODAY,))
today_cnt = cur.fetchone()[0]
if today_cnt == EXPECTED:
    ok(f"오늘({TODAY}) {today_cnt}종목 ✅")
    CHECK_DATE = TODAY
elif today_cnt > 0:
    warn(f"오늘({TODAY}) {today_cnt}종목 (예상 {EXPECTED})")
    CHECK_DATE = TODAY
else:
    warn(f"오늘({TODAY}) 데이터 없음 → 최근 거래일 기준으로 체크 (장 마감 전 정상)")
    cur.execute("SELECT MAX(trade_date) FROM inference_features")
    CHECK_DATE = cur.fetchone()[0]
    warn(f"최근 거래일 {CHECK_DATE} 기준으로 체크")

# 2. 컬럼별 NULL/NaN 체크 (오늘 기준)
print(f"\n  [컬럼별 NULL/NaN 체크 - 오늘({TODAY})]")

# 피처 그룹별 정의
NUMERIC_COLS = {
    # 컬럼명: (허용_null_비율, 허용_nan, 체크_zero)
    "log_ret_1d":           (0.01, False, False),
    "disparity_5d":         (0.01, False, False),
    "disparity_20d":        (0.01, False, False),
    "disparity_60d":        (0.01, False, False),
    "turnover_ratio":       (0.50, False, False),  # market_cap 5/17 이후만 있음
    "volatility_20d":       (0.01, False, False),
    "prop_individual":      (0.10, False, False),
    "prop_foreign":         (0.10, False, False),
    "prop_institution":     (0.10, False, False),
    "per":                  (0.30, False, False),  # 적자기업
    "pbr":                  (0.05, False, False),
    "per_chg_1d":           (0.30, False, False),
    "pbr_chg_1d":           (0.05, False, False),
    "kospi_ret":            (0.00, False, False),
    "kosdaq_ret":           (0.00, False, False),
    "snp500_ret":           (0.00, False, False),
    "nasdaq_ret":           (0.00, False, False),
    "phlx_semi_ret":        (0.00, False, False),
    "vix_chg":              (0.00, False, False),
    "usd_krw_chg":          (0.00, False, False),
    "us_10y_yield_chg":     (0.00, False, False),
    "rate_spread_us_kr":    (0.01, False, False),
    "wti_ret":              (0.00, False, False),
    "gold_ret":             (0.00, False, False),
    "sector_ret_1d":        (0.00, False, False),
    "sector_ret_5d":        (0.00, False, False),
    "sector_ret_20d":       (0.00, False, False),
    "sector_ma_ratio_20d":  (0.00, False, False),
    "sector_volatility":    (0.00, False, False),
    "sector_volume_ratio":  (0.00, False, False),
    "sector_id":            (0.00, False, False),
    "market_id":            (0.00, False, False),
    "day_of_week":          (0.00, False, False),
    "listing_days":         (0.00, False, False),
}

RANGE_CHECKS = {
    "disparity_5d":     (0.5, 2.0),
    "disparity_20d":    (0.5, 2.0),
    "disparity_60d":    (0.3, 3.0),
    "turnover_ratio":   (0.0, 5.0),  # 클리핑 전 이상치 가능
    "volatility_20d":   (0.0, 0.5),
    "prop_individual":  (-1.0, 1.0),  # 클리핑 전 이상치 가능
    "prop_foreign":     (-1.0, 1.0),  # 클리핑 전 이상치 가능
    "prop_institution": (-1.0, 1.0),  # 클리핑 전 이상치 가능
    "day_of_week":      (0, 6),
    "market_id":        (0, 1),
}

for col, (max_null_ratio, allow_nan, check_zero) in NUMERIC_COLS.items():
    cur.execute(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE {col} IS NULL) AS null_cnt,
            COUNT(*) FILTER (WHERE {col}::text = 'NaN') AS nan_cnt,
            COUNT(*) FILTER (WHERE {col} = 0) AS zero_cnt
        FROM inference_features
        WHERE trade_date = %s
    """, (TODAY,))
    r = cur.fetchone()
    total, null_cnt, nan_cnt, zero_cnt = r

    null_ratio = (null_cnt + nan_cnt) / total if total > 0 else 0
    issues = []

    if nan_cnt > 0 and not allow_nan:
        issues.append(f"NaN {nan_cnt}개")
    if null_ratio > max_null_ratio and not allow_nan:
        issues.append(f"NULL {null_cnt}개 ({null_ratio:.1%})")
    if check_zero and zero_cnt == total:
        issues.append(f"전부 0 (market_cap 없는 날짜는 정상)")

    if issues:
        # turnover_ratio 전부 0은 경고로만
        if "전부 0" in str(issues) and col == "turnover_ratio":
            warn(f"{col}: {', '.join(issues)}")
        else:
            err(f"{col}: {', '.join(issues)}")
    elif null_cnt > 0 or nan_cnt > 0:
        ok(f"{col}: NULL {null_cnt}개 / NaN {nan_cnt}개 (허용 범위)")
    else:
        ok(f"{col}: ✅")

# 3. 범위 체크
print(f"\n  [컬럼별 값 범위 체크 - 오늘({TODAY})]")
for col, (min_val, max_val) in RANGE_CHECKS.items():
    cur.execute(f"""
        SELECT
            MIN({col}), MAX({col}), AVG({col}),
            COUNT(*) FILTER (WHERE {col} < {min_val} OR {col} > {max_val}) AS out_of_range
        FROM inference_features
        WHERE trade_date = %s
        AND {col} IS NOT NULL
        AND {col}::text != 'NaN'
    """, (CHECK_DATE,))
    r = cur.fetchone()
    if r[0] is None:
        warn(f"{col}: 값 없음")
        continue
    mn, mx, avg, oor = r
    if oor > 0:
        warn(f"{col}: 범위 벗어남 {oor}개 (min={float(mn):.4f}, max={float(mx):.4f}, 기대=[{min_val},{max_val}])")
    else:
        ok(f"{col}: 범위 정상 (min={float(mn):.4f}, max={float(mx):.4f})")

# 4. 날짜별 종목수 일치
print(f"\n  [날짜별 종목수 체크]")
cur.execute("""
    SELECT trade_date, COUNT(*) AS cnt,
           COUNT(sector_ret_1d) AS has_sector,
           COUNT(prop_individual) AS has_flow,
           COUNT(turnover_ratio) FILTER (WHERE turnover_ratio IS NOT NULL
               AND turnover_ratio::text != 'NaN') AS has_turnover
    FROM inference_features
    GROUP BY trade_date
    ORDER BY trade_date DESC
    LIMIT 10
""")
rows = cur.fetchall()
print(f"  {'날짜':<12} {'종목':>5} {'섹터':>5} {'수급':>5} {'회전율':>6}")
for r in rows:
    line = f"  {str(r[0]):<12} {r[1]:>5} {r[2]:>5} {r[3]:>5} {r[4]:>6}"
    print(line)
    if r[1] != EXPECTED:
        warn(f"inference_features {r[0]}: 종목수 {r[1]} (예상 {EXPECTED})")
    if r[2] == 0:
        err(f"inference_features {r[0]}: 섹터 피처 전부 없음 (조인 실패)")
    if r[4] == 0:
        warn(f"inference_features {r[0]}: turnover_ratio 전부 없음 (market_cap 없는 날 정상)")

# ============================================================
print("\n" + "="*60)
print("realtime_features 검증")
print("="*60)

cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date),
           COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT trade_date)
    FROM realtime_features
""")
r = cur.fetchone()
if not r[0]:
    err("realtime_features: 데이터 없음")
else:
    print(f"  기간: {r[0]} ~ {r[1]} / {r[2]:,}행 / {r[3]}종목 / {r[4]}거래일")

    cur.execute("""
        SELECT COUNT(DISTINCT ticker), COUNT(*),
               MIN(trade_datetime), MAX(trade_datetime)
        FROM realtime_features WHERE trade_date = %s
    """, (TODAY,))
    r = cur.fetchone()
    print(f"  오늘: {r[0]}종목 / {r[1]}봉 ({r[2]} ~ {r[3]})")

    # NULL/NaN 체크
    RT_COLS = [
        "time_progress", "rel_close", "rel_high", "rel_low", "log_ret",
        "disparity_5", "disparity_20", "disparity_60",
        "vol_ratio", "rsi_14", "bb_position",
        "macd_ratio", "macd_signal_ratio", "macd_hist_ratio"
    ]

    print(f"\n  [컬럼별 NULL/NaN 체크 - 오늘({TODAY})]")
    for col in RT_COLS:
        cur.execute(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE {col} IS NULL) AS null_cnt,
                COUNT(*) FILTER (WHERE {col}::text = 'NaN') AS nan_cnt
            FROM realtime_features WHERE trade_date = %s
        """, (TODAY,))
        r = cur.fetchone()
        total, null_cnt, nan_cnt = r
        if null_cnt > 0 or nan_cnt > 0:
            ratio = (null_cnt + nan_cnt) / total
            if ratio > 0.1:
                err(f"{col}: NULL {null_cnt} / NaN {nan_cnt} ({ratio:.1%})")
            else:
                warn(f"{col}: NULL {null_cnt} / NaN {nan_cnt} ({ratio:.1%}) - 워밍업 기간 정상")
        else:
            ok(f"{col}: ✅")

    # 범위 체크
    print(f"\n  [값 범위 체크 - 오늘({TODAY})]")
    RT_RANGES = {
        "time_progress":    (0.0, 1.0),
        "rsi_14":           (0.0, 100.0),
        "bb_position":      (-1.0, 2.0),
        "vol_ratio":        (0.0, 50.0),
        "disparity_5":      (0.5, 2.0),
        "disparity_20":     (0.5, 2.0),
        "disparity_60":     (0.3, 3.0),
    }
    # realtime_features 실제 컬럼 확인
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'realtime_features'")
    rt_actual_cols = {r[0] for r in cur.fetchall()}
    import datetime
    now_hour = datetime.datetime.now().hour
    is_market_hours = 9 <= now_hour <= 15

    for col, (mn, mx) in RT_RANGES.items():
        if col not in rt_actual_cols:
            warn(f"{col}: realtime_features에 없는 컬럼 (skip)")
            continue
        cur.execute(f"""
            SELECT MIN({col}), MAX({col}),
                   COUNT(*) FILTER (WHERE {col} < {mn} OR {col} > {mx}) AS oor
            FROM realtime_features
            WHERE trade_date = %s AND {col} IS NOT NULL AND {col}::text != 'NaN'
        """, (TODAY,))
        r = cur.fetchone()
        if r[0] is None:
            if is_market_hours:
                warn(f"{col}: 장중인데 값 없음")
            else:
                ok(f"{col}: 장외 시간 → 값 없음 정상")
        elif r[2] > 0:
            warn(f"{col}: 범위 벗어남 {r[2]}개 (min={float(r[0]):.3f}, max={float(r[1]):.3f})")
        else:
            ok(f"{col}: 범위 정상 (min={float(r[0]):.3f}, max={float(r[1]):.3f})")

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
elif not errors:
    print("  ✅ 오류 없음 (경고만 있음)")

conn.close()