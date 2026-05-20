import psycopg2
import os
from datetime import date

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", 5432),
    dbname=os.environ.get("DB_NAME", "stock_db"),
    user=os.environ.get("DB_USER", "stock_user"),
    password=os.environ.get("DB_PASSWORD", "your_password"),
)
cur = conn.cursor()
TODAY = date.today()


def check_nulls(table, date_col, date_val, exclude_cols=None):
    """테이블의 모든 컬럼 NULL + NaN 현황 체크"""
    exclude_cols = exclude_cols or []
    cur.execute(f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = '{table}'
        ORDER BY ordinal_position
    """)
    col_info = [(r[0], r[1]) for r in cur.fetchall()
                if r[0] not in exclude_cols]

    checks = []
    for col, dtype in col_info:
        if dtype in ('double precision', 'real', 'numeric'):
            checks.append(
                f"COUNT(*) FILTER (WHERE {col} IS NULL "
                f"OR {col} = 'NaN'::double precision "
                f"OR {col} = 'Infinity'::double precision "
                f"OR {col} = '-Infinity'::double precision) AS {col}"
        )
        else:
            checks.append(
                f"COUNT(*) FILTER (WHERE {col} IS NULL) AS {col}"
            )

    null_checks = ", ".join(checks)
    cur.execute(f"""
        SELECT {null_checks}
        FROM {table}
        WHERE {date_col} = %s
    """, (date_val,))
    row = cur.fetchone()

    print(f"\n  {'컬럼명':<30} {'상태':>10}")
    print(f"  {'-'*42}")
    for (col, dtype), cnt in zip(col_info, row):
        status = "✅" if cnt == 0 else f"⚠️  {cnt}개 NULL/NaN"
        print(f"  {col:<30} {status}")


# ============================================================
print("\n" + "="*60)
print("inference_features (장외 피처)")
print("="*60)

cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date),
           COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT trade_date)
    FROM inference_features
""")
r = cur.fetchone()
print(f"기간:      {r[0]} ~ {r[1]}")
print(f"전체 행수: {r[2]:,}행 / {r[3]}종목 / {r[4]}거래일")

cur.execute("SELECT COUNT(*) FROM inference_features WHERE trade_date = %s", (TODAY,))
today_cnt = cur.fetchone()[0]
print(f"오늘({TODAY}): {'✅' if today_cnt > 0 else '❌'} {today_cnt}종목")

print("\n[오늘 기준 전체 컬럼 NULL 현황]")
check_nulls("inference_features", "trade_date", TODAY,
            exclude_cols=["ticker", "trade_date"])

print("\n[최근 5일 적재 현황]")
cur.execute("""
    SELECT trade_date, COUNT(*) AS cnt,
           COUNT(log_ret_1d) AS tech,
           COUNT(prop_individual) AS flow,
           COUNT(per) AS per_cnt,
           COUNT(kospi_ret) AS macro,
           COUNT(sector_ret_1d) AS sector,
           COUNT(is_bok) AS event,
           COUNT(sector_id) AS static
    FROM inference_features
    GROUP BY trade_date
    ORDER BY trade_date DESC
    LIMIT 5
""")
print(f"  {'날짜':<12} {'종목':>5} {'일봉':>5} {'수급':>5} {'PER':>5} {'매크로':>5} {'섹터':>5} {'이벤트':>6} {'Static':>6}")
for r in cur.fetchall():
    print(f"  {str(r[0]):<12} {r[1]:>5} {r[2]:>5} {r[3]:>5} {r[4]:>5} {r[5]:>5} {r[6]:>5} {r[7]:>6} {r[8]:>6}")

# ============================================================
print("\n" + "="*60)
print("realtime_features (5분봉 피처)")
print("="*60)

cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date),
           COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT trade_date)
    FROM realtime_features
""")
r = cur.fetchone()
if r[0]:
    print(f"기간:      {r[0]} ~ {r[1]}")
    print(f"전체 행수: {r[2]:,}행 / {r[3]}종목 / {r[4]}거래일")

    cur.execute("""
        SELECT COUNT(DISTINCT ticker), COUNT(*),
               MIN(trade_datetime), MAX(trade_datetime)
        FROM realtime_features WHERE trade_date = %s
    """, (TODAY,))
    r = cur.fetchone()
    print(f"오늘({TODAY}): {r[0]}종목 / {r[1]}봉")
    if r[2]:
        print(f"  시작: {r[2]} ~ 마지막: {r[3]}")

    print("\n[오늘 기준 전체 컬럼 NULL 현황]")
    check_nulls("realtime_features", "trade_date", TODAY,
                exclude_cols=["ticker", "trade_datetime", "trade_date"])
else:
    print("데이터 없음")

# ============================================================
print("\n" + "="*60)
print("investor_flow_daily (수급)")
print("="*60)

cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date),
           COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT trade_date)
    FROM investor_flow_daily
""")
r = cur.fetchone()
if r[0]:
    print(f"기간:      {r[0]} ~ {r[1]}")
    print(f"전체 행수: {r[2]:,}행 / {r[3]}종목 / {r[4]}거래일")

    cur.execute("SELECT COUNT(*) FROM investor_flow_daily WHERE trade_date = %s", (TODAY,))
    today_cnt = cur.fetchone()[0]
    print(f"오늘({TODAY}): {'✅' if today_cnt > 0 else '❌'} {today_cnt}종목")

    # 전부 0인 날짜 확인
    cur.execute("""
        SELECT trade_date, COUNT(*) AS cnt,
               SUM(CASE WHEN individual_net_amt = 0 AND foreign_net_amt = 0
                        AND inst_net_amt = 0 THEN 1 ELSE 0 END) AS zero_cnt
        FROM investor_flow_daily
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 10
    """)
    print(f"\n  {'날짜':<12} {'종목수':>6} {'전부0':>6}")
    for r in cur.fetchall():
        flag = " ⚠️" if r[2] > 0 else ""
        print(f"  {str(r[0]):<12} {r[1]:>6} {r[2]:>6}{flag}")
else:
    print("데이터 없음 ❌")

# ============================================================
print("\n" + "="*60)
print("daily_valuation (PER/PBR)")
print("="*60)

cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date),
           COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT trade_date),
           COUNT(per), COUNT(pbr)
    FROM daily_valuation
""")
r = cur.fetchone()
print(f"기간:      {r[0]} ~ {r[1]}")
print(f"전체 행수: {r[2]:,}행 / {r[3]}종목 / {r[4]}거래일")
print(f"PER 있음:  {r[5]:,}행 / PBR 있음: {r[6]:,}행")

cur.execute("SELECT COUNT(*) FROM daily_valuation WHERE trade_date = %s", (TODAY,))
today_cnt = cur.fetchone()[0]
print(f"오늘({TODAY}): {'✅' if today_cnt > 0 else '❌'} {today_cnt}종목")

# ============================================================
print("\n" + "="*60)
print("market_global (글로벌 매크로)")
print("="*60)

cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date), COUNT(*)
    FROM market_global
""")
r = cur.fetchone()
print(f"기간:      {r[0]} ~ {r[1]} / {r[2]}행")

print("\n[전체 컬럼 NULL 현황]")
check_nulls("market_global", "trade_date", TODAY,
            exclude_cols=["trade_date", "created_at"])

# ============================================================
print("\n" + "="*60)
print("market_index_daily (지수)")
print("="*60)

cur.execute("""
    SELECT index_code, MIN(trade_date), MAX(trade_date), COUNT(*)
    FROM market_index_daily
    GROUP BY index_code
    ORDER BY index_code
""")
for r in cur.fetchall():
    print(f"  {r[0]:10s}: {r[1]} ~ {r[2]} ({r[3]}행)")

# ============================================================
print("\n" + "="*60)
print("calendar / market_events / stock_events")
print("="*60)

cur.execute("SELECT MIN(base_date), MAX(base_date), COUNT(*) FROM calendar")
r = cur.fetchone()
print(f"calendar:      {r[0]} ~ {r[1]} / {r[2]}행")

cur.execute("SELECT MIN(event_date), MAX(event_date), COUNT(*) FROM market_events")
r = cur.fetchone()
print(f"market_events: {r[0]} ~ {r[1]} / {r[2]}행")

cur.execute("SELECT MIN(event_date), MAX(event_date), COUNT(*), COUNT(DISTINCT ticker) FROM stock_events")
r = cur.fetchone()
print(f"stock_events:  {r[0]} ~ {r[1]} / {r[2]}행 / {r[3]}종목")

cur.execute("SELECT COUNT(*) FROM stock_events WHERE event_date = %s", (TODAY,))
print(f"오늘 공시:     {cur.fetchone()[0]}건")

# ============================================================
print("\n" + "="*60)
print("ticker_metadata")
print("="*60)

cur.execute("""
    SELECT COUNT(*), COUNT(sector_id), COUNT(market_id), COUNT(listing_date)
    FROM ticker_metadata
""")
r = cur.fetchone()
print(f"전체:        {r[0]}종목")
print(f"sector_id:   {r[1]}개")
print(f"market_id:   {r[2]}개")
print(f"listing_date:{r[3]}개")

conn.close()