"""
init_calendar_events.py
=======================
캘린더 및 시장 이벤트 초기 적재 (1회 실행)
- calendar: 공휴일, 개장일, 공매도금지 여부
- market_events: BOK, FOMC, 선물옵션만기일

실행:
    conda activate kis_collector
    pip install holidays pandas_market_calendars
    python ~/init_calendar_events.py
"""

import os
import logging
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from datetime import timedelta

try:
    import holidays
    import pandas_market_calendars as mcal
    HAS_CALENDAR_LIBS = True
except ImportError:
    HAS_CALENDAR_LIBS = False
    print("⚠️ holidays/pandas_market_calendars 없음 → 수동 캘린더 생성")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("init_calendar_events.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

# 공매도 금지 기간
SHORT_SELLING_BAN = [
    ("2020-03-16", "2021-05-02"),  # 코로나 전면 금지
    ("2023-11-06", "2099-12-31"),  # 현재 전면 금지 중
]

# BOK 금통위 일정
BOK_DATES = [
    # 2020
    '2020-01-17', '2020-02-27', '2020-04-09', '2020-05-28',
    '2020-07-16', '2020-08-27', '2020-10-14', '2020-11-26',
    # 2021
    '2021-01-15', '2021-02-25', '2021-04-15', '2021-05-27',
    '2021-07-15', '2021-08-26', '2021-10-12', '2021-11-25',
    # 2022
    '2022-01-14', '2022-02-24', '2022-04-14', '2022-05-26',
    '2022-07-13', '2022-08-25', '2022-10-12', '2022-11-24',
    # 2023
    '2023-01-13', '2023-02-23', '2023-04-11', '2023-05-25',
    '2023-07-13', '2023-08-24', '2023-10-19', '2023-11-30',
    # 2024
    '2024-01-11', '2024-02-22', '2024-04-12', '2024-05-23',
    '2024-07-11', '2024-08-22', '2024-10-11', '2024-11-28',
    # 2025
    '2025-01-16', '2025-02-20', '2025-04-10', '2025-05-29',
    '2025-07-17', '2025-08-28', '2025-10-23', '2025-11-27',
    # 2026
    '2026-01-16', '2026-02-26', '2026-04-09', '2026-05-28',
    '2026-07-16', '2026-08-27', '2026-10-15', '2026-11-26',
]

# FOMC (미국 현지 기준, 한국 반영일 = +1일)
FOMC_DATES_US = [
    # 2020
    '2020-01-29', '2020-03-03', '2020-03-15', '2020-04-29',
    '2020-06-10', '2020-07-29', '2020-09-16', '2020-11-05', '2020-12-16',
    # 2021
    '2021-01-27', '2021-03-17', '2021-04-28', '2021-06-16',
    '2021-07-28', '2021-09-22', '2021-11-03', '2021-12-15',
    # 2022
    '2022-01-26', '2022-03-16', '2022-05-04', '2022-06-15',
    '2022-07-27', '2022-09-21', '2022-11-02', '2022-12-14',
    # 2023
    '2023-02-01', '2023-03-22', '2023-05-03', '2023-06-14',
    '2023-07-26', '2023-09-20', '2023-11-01', '2023-12-13',
    # 2024
    '2024-01-31', '2024-03-20', '2024-05-01', '2024-06-12',
    '2024-07-31', '2024-09-18', '2024-11-07', '2024-12-18',
    # 2025
    '2025-01-29', '2025-03-19', '2025-05-07', '2025-06-18',
    '2025-07-30', '2025-09-17', '2025-10-29', '2025-12-10',
    # 2026
    '2026-01-28', '2026-03-18', '2026-04-29', '2026-06-17',
    '2026-07-29', '2026-09-16', '2026-11-04', '2026-12-16',
]

# 한국 선물옵션 만기일
WITCHING_KR_DATES = [
    # 2020
    '2020-03-12', '2020-06-11', '2020-09-10', '2020-12-10',
    # 2021
    '2021-03-11', '2021-06-10', '2021-09-09', '2021-12-09',
    # 2022
    '2022-03-10', '2022-06-09', '2022-09-08', '2022-12-08',
    # 2023
    '2023-03-09', '2023-06-08', '2023-09-14', '2023-12-14',
    # 2024
    '2024-03-14', '2024-06-13', '2024-09-12', '2024-12-12',
    # 2025
    '2025-03-13', '2025-06-12', '2025-09-11', '2025-12-11',
    # 2026
    '2026-03-12', '2026-06-11', '2026-09-10', '2026-12-10',
]

# 미국 선물옵션 만기일 (미국 현지 기준, +1일)
WITCHING_US_DATES = [
    # 2020
    '2020-03-20', '2020-06-19', '2020-09-18', '2020-12-18',
    # 2021
    '2021-03-19', '2021-06-18', '2021-09-17', '2021-12-17',
    # 2022
    '2022-03-18', '2022-06-17', '2022-09-16', '2022-12-16',
    # 2023
    '2023-03-17', '2023-06-16', '2023-09-15', '2023-12-15',
    # 2024
    '2024-03-15', '2024-06-21', '2024-09-20', '2024-12-20',
    # 2025
    '2025-03-21', '2025-06-20', '2025-09-19', '2025-12-19',
    # 2026
    '2026-03-20', '2026-06-19', '2026-09-18', '2026-12-18',
]


# ============================================================
# 1. 캘린더 생성
# ============================================================
def build_calendar(start_year=2020, end_year=2026) -> pd.DataFrame:
    logger.info("캘린더 생성 중...")
    date_range = pd.date_range(
        start=f"{start_year}-01-01",
        end=f"{end_year}-12-31"
    )
    df = pd.DataFrame({"base_date": date_range})
    df["day_of_week"] = df["base_date"].dt.dayofweek  # 0=월 ~ 6=일

    # 공휴일 (holidays 라이브러리)
    if HAS_CALENDAR_LIBS:
        kr_holidays = holidays.KR(years=range(start_year, end_year + 1))
        df["is_holiday"] = df["base_date"].apply(
            lambda x: 1 if x in kr_holidays else 0
        )
        # 한국 개장일 (NYSE 대신 주말 + 공휴일 제외로 근사)
        df["is_market_open"] = df.apply(
            lambda r: 1 if r["day_of_week"] < 5 and r["is_holiday"] == 0 else 0,
            axis=1
        )
    else:
        # 라이브러리 없으면 주말만 제외
        df["is_holiday"] = 0
        df["is_market_open"] = df["day_of_week"].apply(lambda x: 1 if x < 5 else 0)

    # 공매도 금지 여부
    df["is_short_selling_banned"] = 0
    for start_str, end_str in SHORT_SELLING_BAN:
        mask = (df["base_date"] >= start_str) & (df["base_date"] <= end_str)
        df.loc[mask, "is_short_selling_banned"] = 1

    df["base_date"] = df["base_date"].dt.date
    logger.info(f"캘린더 생성 완료: {len(df)}행")
    return df


# ============================================================
# 2. 시장 이벤트 생성
# ============================================================
def build_market_events() -> pd.DataFrame:
    logger.info("시장 이벤트 생성 중...")
    rows = []

    # BOK (시차 없음)
    for d in BOK_DATES:
        rows.append({"event_date": d, "event_type": "BOK",
                     "is_bok": 1, "is_fomc": 0,
                     "is_witching_kr": 0, "is_witching_us": 0})

    # FOMC (한국 반영일 = 미국 발표일 + 1)
    for d in FOMC_DATES_US:
        kr_date = (pd.to_datetime(d) + timedelta(days=1)).strftime("%Y-%m-%d")
        rows.append({"event_date": kr_date, "event_type": "FOMC",
                     "is_bok": 0, "is_fomc": 1,
                     "is_witching_kr": 0, "is_witching_us": 0})

    # 한국 만기일
    for d in WITCHING_KR_DATES:
        rows.append({"event_date": d, "event_type": "WITCHING_KR",
                     "is_bok": 0, "is_fomc": 0,
                     "is_witching_kr": 1, "is_witching_us": 0})

    # 미국 만기일 (한국 반영일 = +1)
    for d in WITCHING_US_DATES:
        kr_date = (pd.to_datetime(d) + timedelta(days=1)).strftime("%Y-%m-%d")
        rows.append({"event_date": kr_date, "event_type": "WITCHING_US",
                     "is_bok": 0, "is_fomc": 0,
                     "is_witching_kr": 0, "is_witching_us": 1})

    df = pd.DataFrame(rows)
    df["event_date"] = pd.to_datetime(df["event_date"]).dt.date
    logger.info(f"시장 이벤트 생성 완료: {len(df)}행")
    return df


# ============================================================
# 3. DB 저장
# ============================================================
def save_to_db(df_cal: pd.DataFrame, df_events: pd.DataFrame):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        # 테이블 생성
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar (
                base_date               DATE    NOT NULL,
                day_of_week             INT,
                is_market_open          INT,
                is_holiday              INT,
                is_short_selling_banned INT,
                PRIMARY KEY (base_date)
            );

            CREATE TABLE IF NOT EXISTS market_events (
                event_date      DATE        NOT NULL,
                event_type      VARCHAR(20) NOT NULL,
                is_bok          INT DEFAULT 0,
                is_fomc         INT DEFAULT 0,
                is_witching_kr  INT DEFAULT 0,
                is_witching_us  INT DEFAULT 0,
                PRIMARY KEY (event_date, event_type)
            );
        """)
        conn.commit()

        # 캘린더 저장
        cal_rows = [
            (row["base_date"], int(row["day_of_week"]),
             int(row["is_market_open"]), int(row["is_holiday"]),
             int(row["is_short_selling_banned"]))
            for _, row in df_cal.iterrows()
        ]
        execute_values(cur, """
            INSERT INTO calendar
                (base_date, day_of_week, is_market_open,
                 is_holiday, is_short_selling_banned)
            VALUES %s
            ON CONFLICT (base_date) DO UPDATE SET
                is_market_open          = EXCLUDED.is_market_open,
                is_holiday              = EXCLUDED.is_holiday,
                is_short_selling_banned = EXCLUDED.is_short_selling_banned
        """, cal_rows)
        conn.commit()
        logger.info(f"calendar 저장 완료: {len(cal_rows)}행")

        # 시장 이벤트 저장
        event_rows = [
            (row["event_date"], row["event_type"],
             int(row["is_bok"]), int(row["is_fomc"]),
             int(row["is_witching_kr"]), int(row["is_witching_us"]))
            for _, row in df_events.iterrows()
        ]
        execute_values(cur, """
            INSERT INTO market_events
                (event_date, event_type, is_bok, is_fomc,
                 is_witching_kr, is_witching_us)
            VALUES %s
            ON CONFLICT (event_date, event_type) DO NOTHING
        """, event_rows)
        conn.commit()
        logger.info(f"market_events 저장 완료: {len(event_rows)}행")

    except Exception as e:
        conn.rollback()
        logger.error(f"DB 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


# ============================================================
# 메인
# ============================================================
def main():
    logger.info("===== 캘린더/시장이벤트 초기 적재 시작 =====")

    df_cal    = build_calendar(start_year=2020, end_year=2026)
    df_events = build_market_events()
    save_to_db(df_cal, df_events)

    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()