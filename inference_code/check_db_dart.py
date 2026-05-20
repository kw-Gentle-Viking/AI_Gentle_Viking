import psycopg2
import os
from datetime import date, timedelta

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", 5432),
    dbname=os.environ.get("DB_NAME", "stock_db"),
    user=os.environ.get("DB_USER", "stock_user"),
    password=os.environ.get("DB_PASSWORD", "your_password"),
)
cur = conn.cursor()

print("\n" + "="*60)
print("stock_events 수집 현황")
print("="*60)

# 전체 요약
cur.execute("""
    SELECT
        MIN(event_date) AS start_date,
        MAX(event_date) AS end_date,
        COUNT(*) AS total_rows,
        COUNT(DISTINCT ticker) AS ticker_cnt
    FROM stock_events
""")
row = cur.fetchone()
print(f"기간:       {row[0]} ~ {row[1]}")
print(f"전체 행수:  {row[2]:,}행")
print(f"종목수:     {row[3]}개")

# 이벤트 타입별 집계
print("\n[이벤트 타입별]")
cur.execute("""
    SELECT event_type, COUNT(*) AS cnt
    FROM stock_events
    GROUP BY event_type
    ORDER BY cnt DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:20s}: {r[1]:,}건")

# 최근 7일 수집 현황
print("\n[최근 7일 수집 현황]")
cur.execute("""
    SELECT event_date, COUNT(*) AS cnt
    FROM stock_events
    WHERE event_date >= %s
    GROUP BY event_date
    ORDER BY event_date DESC
""", (date.today() - timedelta(days=7),))
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  {r[0]}: {r[1]}건")
else:
    print("  최근 7일 이벤트 없음")

# 오늘 수집 여부
cur.execute("SELECT COUNT(*) FROM stock_events WHERE event_date = %s", (date.today(),))
today_cnt = cur.fetchone()[0]
status = "✅" if today_cnt > 0 else "➖ (오늘 공시 없음)"
print(f"\n오늘({date.today()}) 수집: {status} {today_cnt}건")

# calendar 확인
print("\n" + "="*60)
print("calendar 현황")
print("="*60)
cur.execute("""
    SELECT
        MIN(base_date) AS start_date,
        MAX(base_date) AS end_date,
        COUNT(*) AS total_rows,
        SUM(is_market_open) AS market_open_days,
        SUM(is_short_selling_banned) AS banned_days
    FROM calendar
""")
row = cur.fetchone()
print(f"기간:           {row[0]} ~ {row[1]}")
print(f"전체 행수:      {row[2]:,}행")
print(f"개장일수:       {row[3]:,}일")
print(f"공매도금지일수: {row[4]:,}일")

# market_events 확인
print("\n" + "="*60)
print("market_events 현황")
print("="*60)
cur.execute("""
    SELECT event_type, COUNT(*) AS cnt
    FROM market_events
    GROUP BY event_type
    ORDER BY event_type
""")
for r in cur.fetchall():
    print(f"  {r[0]:15s}: {r[1]}건")

cur.execute("""
    SELECT MIN(event_date), MAX(event_date), COUNT(*)
    FROM market_events
""")
row = cur.fetchone()
print(f"\n기간: {row[0]} ~ {row[1]} / 총 {row[2]}건")

conn.close()