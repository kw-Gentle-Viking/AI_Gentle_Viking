"""
check_daily_kis.py
==================
한투 API 수집 데이터 DB 확인
"""

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

# ── daily_valuation ─────────────────────────────────────────
print("\n" + "="*60)
print("daily_valuation (PER/PBR/시가총액)")
print("="*60)
cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date), COUNT(*),
           COUNT(DISTINCT ticker),
           COUNT(per), COUNT(pbr), COUNT(market_cap)
    FROM daily_valuation
""")
r = cur.fetchone()
print(f"기간:       {r[0]} ~ {r[1]}")
print(f"전체 행수:  {r[2]:,}행 / {r[3]}종목")
print(f"PER 있음:   {r[4]:,}행")
print(f"PBR 있음:   {r[5]:,}행")
print(f"시가총액:   {r[6]:,}행")

cur.execute("SELECT COUNT(*) FROM daily_valuation WHERE trade_date = %s", (TODAY,))
today_cnt = cur.fetchone()[0]
print(f"오늘({TODAY}): {'✅' if today_cnt >= 340 else '⚠️'} {today_cnt}종목")

# ── investor_flow_daily ──────────────────────────────────────
print("\n" + "="*60)
print("investor_flow_daily (수급)")
print("="*60)
cur.execute("""
    SELECT MIN(trade_date), MAX(trade_date), COUNT(*),
           COUNT(DISTINCT ticker)
    FROM investor_flow_daily
""")
r = cur.fetchone()
if r[0]:
    print(f"기간:       {r[0]} ~ {r[1]}")
    print(f"전체 행수:  {r[2]:,}행 / {r[3]}종목")
else:
    print("데이터 없음 ❌")

cur.execute("SELECT COUNT(*) FROM investor_flow_daily WHERE trade_date = %s", (TODAY,))
today_cnt = cur.fetchone()[0]
print(f"오늘({TODAY}): {'✅' if today_cnt >= 340 else '⚠️  ' + str(today_cnt) + '종목'}")

# ── market_index_daily ───────────────────────────────────────
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

cur.execute("""
    SELECT index_code, close_price
    FROM market_index_daily
    WHERE trade_date = %s
    ORDER BY index_code
""", (TODAY,))
rows = cur.fetchall()
if rows:
    print(f"\n오늘({TODAY}) ✅")
    for r in rows:
        print(f"  {r[0]}: {r[1]:,.2f}")
else:
    print(f"\n오늘({TODAY}) ❌")

conn.close()