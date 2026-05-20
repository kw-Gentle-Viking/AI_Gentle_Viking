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

print("\n" + "="*60)
print("market_global 수집 현황")
print("="*60)

# 전체 요약
cur.execute("""
    SELECT
        MIN(trade_date) AS start_date,
        MAX(trade_date) AS end_date,
        COUNT(*) AS total_rows,
        COUNT(snp500_close) AS snp_cnt,
        COUNT(usd_krw) AS usd_krw_cnt,
        COUNT(us_10y_yield) AS yield_cnt,
        COUNT(wti_crude_oil) AS wti_cnt
    FROM market_global
""")
row = cur.fetchone()
print(f"기간:         {row[0]} ~ {row[1]}")
print(f"전체 행수:    {row[2]}행")
print(f"S&P500:       {row[3]}행")
print(f"원달러:       {row[4]}행")
print(f"미국채10년:   {row[5]}행")
print(f"WTI:          {row[6]}행")

# 오늘 수집 여부
cur.execute("SELECT COUNT(*) FROM market_global WHERE trade_date = %s", (date.today(),))
today_cnt = cur.fetchone()[0]
status = "✅" if today_cnt > 0 else "❌"
print(f"\n오늘({date.today()}) 수집: {status}")

# NULL 확인
cur.execute("""
    SELECT
        COUNT(*) FILTER (WHERE snp500_close IS NULL)    AS null_snp,
        COUNT(*) FILTER (WHERE nasdaq_close IS NULL)    AS null_nasdaq,
        COUNT(*) FILTER (WHERE phlx_semi_close IS NULL) AS null_phlx,
        COUNT(*) FILTER (WHERE vix IS NULL)             AS null_vix,
        COUNT(*) FILTER (WHERE usd_krw IS NULL)         AS null_usd,
        COUNT(*) FILTER (WHERE us_10y_yield IS NULL)    AS null_yield,
        COUNT(*) FILTER (WHERE wti_crude_oil IS NULL)   AS null_wti,
        COUNT(*) FILTER (WHERE gold_price IS NULL)      AS null_gold,
        COUNT(*) FILTER (WHERE fed_rate IS NULL)        AS null_fed,
        COUNT(*) FILTER (WHERE kr_base_rate IS NULL)    AS null_kr
    FROM market_global
""")
row = cur.fetchone()
cols = ["snp500", "nasdaq", "phlx_semi", "vix", "usd_krw",
        "us_10y_yield", "wti", "gold", "fed_rate", "kr_base_rate"]
print("\n[NULL 현황]")
for col, cnt in zip(cols, row):
    status = "✅" if cnt == 0 else f"⚠️  {cnt}개"
    print(f"  {col:20s}: {status}")

# 최근 5일
print("\n[최근 5일]")
cur.execute("""
    SELECT trade_date, snp500_close, usd_krw, us_10y_yield, wti_crude_oil, vix
    FROM market_global
    ORDER BY trade_date DESC
    LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {r[0]} | S&P {r[1]:.1f} | USD/KRW {r[2]:.1f} | 10Y {r[3]:.2f} | WTI {r[4]:.1f} | VIX {r[5]:.1f}")

conn.close()