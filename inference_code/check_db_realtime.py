"""
check_realtime.py
=================
실시간 수집 확인용 스크립트
- 하드코딩된 실시간 종목(005930, 000660)의 오늘 5분봉 수집 현황 확인
- 장중에 주기적으로 실행해서 실시간으로 쌓이는지 확인

실행:
conda activate kis_collector
python check_realtime.py
"""

import os
import psycopg2
import psycopg2.extras
import pandas as pd
from datetime import date, datetime

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

REALTIME_TICKERS = ["005930", "000660"]
TODAY = date.today()


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ============================================================
# 1. 오늘 5분봉 건수 확인
# ============================================================
def check_5min_count():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*50)
    print(f"실시간 수집 현황 ({TODAY} / {datetime.now().strftime('%H:%M:%S')})")
    print("="*50)

    for ticker in REALTIME_TICKERS:
        cur.execute("""
            SELECT COUNT(*) FROM intraday_5min
            WHERE ticker = %s AND DATE(datetime) = %s
        """, (ticker, TODAY))
        cnt_5min = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM intraday_1min
            WHERE ticker = %s AND DATE(datetime) = %s
        """, (ticker, TODAY))
        cnt_1min = cur.fetchone()[0]

        status = "✅" if cnt_5min > 0 else "❌"
        print(f"{ticker} | 1분봉: {cnt_1min:>4}개 | 5분봉: {status} {cnt_5min:>3}개")

    cur.close()
    conn.close()


# ============================================================
# 2. 최근 저장된 5분봉 샘플 (가장 최근 3개)
# ============================================================
def check_latest_5min():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    print("\n" + "="*50)
    print("최근 저장된 5분봉 (종목별 최근 3개)")
    print("="*50)

    for ticker in REALTIME_TICKERS:
        cur.execute("""
            SELECT datetime, open, high, low, close, volume
            FROM intraday_5min
            WHERE ticker = %s AND DATE(datetime) = %s
            ORDER BY datetime DESC
            LIMIT 3
        """, (ticker, TODAY))
        rows = cur.fetchall()

        if not rows:
            print(f"\n{ticker}: 데이터 없음 ❌")
        else:
            df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
            print(f"\n{ticker}:")
            print(df.to_string(index=False))

    cur.close()
    conn.close()


# ============================================================
# 3. 5분봉 마지막 저장 시각 확인
# ============================================================
def check_last_update():
    conn = get_conn()
    cur = conn.cursor()

    print("\n" + "="*50)
    print("마지막 5분봉 저장 시각")
    print("="*50)

    for ticker in REALTIME_TICKERS:
        cur.execute("""
            SELECT MAX(datetime) FROM intraday_5min
            WHERE ticker = %s AND DATE(datetime) = %s
        """, (ticker, TODAY))
        last_dt = cur.fetchone()[0]

        if last_dt:
            diff = datetime.now() - last_dt
            minutes = int(diff.total_seconds() // 60)
            print(f"{ticker} | 마지막: {last_dt.strftime('%H:%M:%S')} ({minutes}분 전)")
        else:
            print(f"{ticker} | 아직 없음 ❌")

    cur.close()
    conn.close()


# ============================================================
# 메인
# ============================================================
if __name__ == "__main__":
    try:
        check_5min_count()
        check_latest_5min()
        check_last_update()
    except Exception as e:
        print(f"\nDB 연결 실패: {e}")
        print("환경변수 확인: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD")