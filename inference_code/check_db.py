"""
check_db.py
===========
DB 적재 확인용 스크립트

실행:
conda activate kis_collector
python check_db.py
"""

import os
import psycopg2
import pandas as pd
from datetime import date

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

TODAY = date.today()


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ============================================================
# 1. 5분봉 현황 요약
# ============================================================
def check_5min_by_date():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*60)
    print("5분봉 현황 요약")
    print("="*60)

    cur.execute("""
        SELECT
            DATE(datetime)         AS trade_date,
            COUNT(DISTINCT ticker) AS ticker_cnt,
            COUNT(*)               AS total_5min
        FROM intraday_5min
        GROUP BY DATE(datetime)
        ORDER BY trade_date DESC
    """)
    rows = cur.fetchall()

    if not rows:
        print("데이터 없음 ❌")
    else:
        df = pd.DataFrame(rows, columns=["날짜", "종목수", "전체_5분봉수"])
        total_days = len(df)
        total_rows = df["전체_5분봉수"].sum()
        min_date = df["날짜"].min()
        max_date = df["날짜"].max()
        avg_tickers = df["종목수"].mean()

        print(f"기간:       {min_date} ~ {max_date} ({total_days}거래일)")
        print(f"총 행수:    {total_rows:,}행")
        print(f"평균 종목수: {avg_tickers:.0f}개/일")

        # 오늘 현황
        today_row = df[df["날짜"] == TODAY]
        if not today_row.empty:
            t = today_row.iloc[0]
            print(f"오늘:       ✅ {t['종목수']}개 종목 / {t['전체_5분봉수']:,}행")
        else:
            print(f"오늘:       ❌ 데이터 없음")

        # 최근 5일
        print("\n[최근 5일]")
        for _, r in df.head(5).iterrows():
            status = "✅" if r["종목수"] >= 340 else "⚠️"
            print(f"  {status} {r['날짜']} | {r['종목수']}종목 | {r['전체_5분봉수']:,}행")

    cur.close()
    conn.close()


# ============================================================
# 2. 1분봉 현황 요약
# ============================================================
def check_1min_by_date():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*60)
    print("1분봉 현황 요약 (버퍼)")
    print("="*60)

    cur.execute("""
        SELECT
            DATE(datetime)         AS trade_date,
            COUNT(DISTINCT ticker) AS ticker_cnt,
            COUNT(*)               AS total_1min
        FROM intraday_1min
        GROUP BY DATE(datetime)
        ORDER BY trade_date DESC
        LIMIT 5
    """)
    rows = cur.fetchall()

    if not rows:
        print("데이터 없음 ✅ (버퍼 정상 초기화)")
    else:
        for r in rows:
            print(f"  {r[0]} | {r[1]}종목 | {r[2]:,}행")

    cur.close()
    conn.close()


# ============================================================
# 3. 오늘 수집 현황 요약
# ============================================================
def check_today_detail():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*60)
    print(f"오늘({TODAY}) 수집 현황")
    print("="*60)

    cur.execute("""
        SELECT
            COUNT(DISTINCT a.ticker)                                       AS total_tickers,
            COUNT(DISTINCT CASE WHEN b.cnt_5min > 0 THEN a.ticker END)    AS ok_tickers,
            COUNT(DISTINCT CASE WHEN b.cnt_5min = 0 OR b.cnt_5min IS NULL
                                THEN a.ticker END)                         AS missing_tickers
        FROM (
            SELECT ticker, COUNT(*) AS cnt_1min
            FROM intraday_1min
            WHERE DATE(datetime) = %s
            GROUP BY ticker
        ) a
        LEFT JOIN (
            SELECT ticker, COUNT(*) AS cnt_5min
            FROM intraday_5min
            WHERE DATE(datetime) = %s
            GROUP BY ticker
        ) b ON a.ticker = b.ticker
    """, (TODAY, TODAY))
    row = cur.fetchone()

    if not row or row[0] == 0:
        print("데이터 없음 ❌")
    else:
        print(f"1분봉 수집 종목: {row[0]}개")
        print(f"5분봉 정상:      ✅ {row[1]}개")
        print(f"5분봉 없음:      ❌ {row[2]}개")

    cur.close()
    conn.close()


# ============================================================
# 4. 섹터 일봉 현황 요약
# ============================================================
def check_sector():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*60)
    print("섹터 일봉 현황 요약")
    print("="*60)

    cur.execute("""
        SELECT
            MIN(trade_date) AS min_date,
            MAX(trade_date) AS max_date,
            COUNT(DISTINCT trade_date) AS days,
            AVG(cnt) AS avg_sectors
        FROM (
            SELECT trade_date, COUNT(*) AS cnt
            FROM sector_daily_ohlcv
            GROUP BY trade_date
        ) t
    """)
    row = cur.fetchone()

    if not row or row[0] is None:
        print("데이터 없음 ❌")
    else:
        print(f"기간:        {row[0]} ~ {row[1]} ({row[2]}거래일)")
        print(f"평균 섹터수: {row[3]:.0f}개/일")

    # 오늘 섹터 확인
    cur.execute("SELECT COUNT(*) FROM sector_daily_ohlcv WHERE trade_date = %s", (TODAY,))
    today_cnt = cur.fetchone()[0]
    status = "✅" if today_cnt > 0 else "❌"
    print(f"오늘:        {status} {today_cnt}개 섹터")

    cur.close()
    conn.close()


# ============================================================
# 5. 일봉 현황 요약
# ============================================================
def check_daily():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*60)
    print("종목 일봉 현황 요약")
    print("="*60)

    cur.execute("""
        SELECT
            MIN(trade_date) AS min_date,
            MAX(trade_date) AS max_date,
            COUNT(DISTINCT trade_date) AS days,
            COUNT(DISTINCT ticker) AS tickers,
            COUNT(*) AS total_rows
        FROM price_daily
    """)
    row = cur.fetchone()

    if not row or row[0] is None:
        print("데이터 없음 ❌")
    else:
        print(f"기간:       {row[0]} ~ {row[1]} ({row[2]}거래일)")
        print(f"종목수:     {row[3]}개")
        print(f"총 행수:    {row[4]:,}행")

    # 오늘 일봉 확인
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM price_daily WHERE trade_date = %s", (TODAY,))
    today_cnt = cur.fetchone()[0]
    status = "✅" if today_cnt >= 340 else ("⚠️" if today_cnt > 0 else "❌")
    print(f"오늘:       {status} {today_cnt}개 종목")
    if 0 < today_cnt < 350:
        print(f"            (누락 약 {350 - today_cnt}개)")

    # 최근 5일
    cur.execute("""
        SELECT trade_date, COUNT(DISTINCT ticker) AS cnt
        FROM price_daily
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 5
    """)
    print("\n[최근 5일]")
    for r in cur.fetchall():
        status = "✅" if r[1] >= 340 else "⚠️"
        print(f"  {status} {r[0]} | {r[1]}종목")

    cur.close()
    conn.close()


# ============================================================
# 6. 누락/불완전 종목 요약
# ============================================================
def check_missing_tickers():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*60)
    print("5분봉 불완전 종목 요약 (79개 미만)")
    print("="*60)

    cur.execute("""
        SELECT
            trade_date,
            COUNT(*) AS incomplete_cnt,
            MIN(candle_cnt) AS min_candles,
            MAX(candle_cnt) AS max_candles
        FROM (
            SELECT DATE(datetime) AS trade_date, COUNT(*) AS candle_cnt
            FROM intraday_5min
            GROUP BY DATE(datetime), ticker
            HAVING COUNT(*) < 79
        ) t
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 10
    """)
    rows = cur.fetchall()

    if not rows:
        print("없음 ✅ (모든 종목 정상)")
    else:
        print(f"(최근 10일 기준)")
        for r in rows:
            print(f"  {r[0]} | 불완전 {r[1]}개 종목 | 최소 {r[2]}봉 ~ 최대 {r[3]}봉")

    cur.close()
    conn.close()


# ============================================================
# 메인
# ============================================================
if __name__ == "__main__":
    try:
        check_5min_by_date()
        check_1min_by_date()
        check_today_detail()
        check_sector()
        check_daily()
        check_missing_tickers()
    except Exception as e:
        print(f"\nDB 연결 실패: {e}")
        print("환경변수 확인: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD")