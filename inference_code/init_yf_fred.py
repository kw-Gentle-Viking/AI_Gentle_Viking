"""
init_yf_fred.py
===============
글로벌 시장 데이터 초기 적재 (1회 실행)
- 2026-01-01 ~ 오늘까지 전체 수집
- Yahoo Finance + FRED

수정사항:
    - FRED ffill을 날짜 변환 전에 적용 (kr_base_rate 월별 업데이트 대응)
    - NaN → None 변환 강화 (DB에 NaN 저장 방지)
    - ON CONFLICT 시 COALESCE로 기존값 보존
"""

import os
import time
import logging
import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from datetime import date, timedelta
import pandas as pd
import yfinance as yf
import pandas_datareader.data as web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("init_global.log"),
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

START_DATE = date(2026, 1, 1)
END_DATE   = date.today()

YF_TICKERS = {
    "^GSPC": "snp500_close",
    "^IXIC": "nasdaq_close",
    "^SOX":  "phlx_semi_close",
    "^VIX":  "vix",
    "CL=F":  "wti_crude_oil",
    "GC=F":  "gold_price",
}

FRED_TICKERS = {
    "DEXKOUS":       "usd_krw",
    "DGS10":         "us_10y_yield",
    "DFEDTARL":      "fed_rate",
    "INTDSRKRM193N": "kr_base_rate",
}

ALL_COLS = list(YF_TICKERS.values()) + list(FRED_TICKERS.values())


def nan_to_none(val):
    if val is None:
        return None
    try:
        if np.isnan(float(val)) or np.isinf(float(val)):
            return None
    except (TypeError, ValueError):
        pass
    return val


def fetch_yahoo() -> pd.DataFrame:
    logger.info("Yahoo Finance 수집 시작...")
    for attempt in range(3):
        try:
            df = yf.download(
                list(YF_TICKERS.keys()),
                start=START_DATE.strftime("%Y-%m-%d"),
                end=(END_DATE + timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=True,
            )["Close"]
            if isinstance(df, pd.Series):
                df = df.to_frame()
            df = df.rename(columns=YF_TICKERS)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            # ffill 먼저 (주말/공휴일 결측치)
            df = df.ffill()
            # 미국 날짜 → 한국 날짜 (+1일)
            df.index = (df.index + timedelta(days=1)).date
            df.index.name = "trade_date"
            nan_cnt = df.isna().sum().sum()
            if nan_cnt > 0:
                logger.warning(f"Yahoo NaN 잔존: {nan_cnt}개")
            logger.info(f"Yahoo Finance 수집 완료: {len(df)}행")
            return df
        except Exception as e:
            logger.warning(f"Yahoo Finance 실패 (attempt {attempt+1}): {e}")
            time.sleep(3)
    return pd.DataFrame()


def fetch_fred() -> pd.DataFrame:
    logger.info("FRED 수집 시작...")
    for attempt in range(3):
        try:
            # kr_base_rate 월별이라 시작일 60일 전부터 조회
            fred_start = START_DATE - timedelta(days=60)
            df = web.DataReader(
                list(FRED_TICKERS.keys()), "fred",
                fred_start.strftime("%Y-%m-%d"),
                END_DATE.strftime("%Y-%m-%d"),
            )
            df = df.rename(columns=FRED_TICKERS)
            df.index = pd.to_datetime(df.index)
            # ffill 먼저 (FRED 주말/공휴일 + kr_base_rate 월별)
            df = df.ffill()
            # 미국 날짜 → 한국 날짜 (+1일)
            df.index = (df.index + timedelta(days=1)).date
            df.index.name = "trade_date"
            # START_DATE 이후만 필터링
            df = df[df.index >= START_DATE]
            nan_cnt = df.isna().sum().sum()
            if nan_cnt > 0:
                logger.warning(f"FRED NaN 잔존: {nan_cnt}개 → {df.isna().sum().to_dict()}")
            logger.info(f"FRED 수집 완료: {len(df)}행")
            return df
        except Exception as e:
            logger.warning(f"FRED 실패 (attempt {attempt+1}): {e}")
            time.sleep(3)
    return pd.DataFrame()


def merge_and_save(df_yf: pd.DataFrame, df_fred: pd.DataFrame):
    if df_yf.empty and df_fred.empty:
        logger.error("수집 데이터 없음")
        return

    if df_yf.empty:
        df = df_fred
    elif df_fred.empty:
        df = df_yf
    else:
        df = df_yf.join(df_fred, how="outer")

    # 최종 ffill
    df = df.ffill()
    df = df.reset_index()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 주말 제거 (한국 장 없는 날)
    df = df[df["trade_date"].dt.dayofweek < 5]  # 0=월 ~ 4=금

    # 누락 컬럼 보완
    for col in ALL_COLS:
        if col not in df.columns:
            df[col] = None

    # NaN → None 변환
    for col in ALL_COLS:
        df[col] = df[col].apply(nan_to_none)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_global (
                trade_date      DATE             NOT NULL,
                snp500_close    DOUBLE PRECISION,
                nasdaq_close    DOUBLE PRECISION,
                phlx_semi_close DOUBLE PRECISION,
                vix             DOUBLE PRECISION,
                wti_crude_oil   DOUBLE PRECISION,
                gold_price      DOUBLE PRECISION,
                usd_krw         DOUBLE PRECISION,
                us_10y_yield    DOUBLE PRECISION,
                fed_rate        DOUBLE PRECISION,
                kr_base_rate    DOUBLE PRECISION,
                created_at      TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (trade_date)
            );
        """)
        conn.commit()

        rows = [
            (
                row["trade_date"],
                nan_to_none(row.get("snp500_close")),
                nan_to_none(row.get("nasdaq_close")),
                nan_to_none(row.get("phlx_semi_close")),
                nan_to_none(row.get("vix")),
                nan_to_none(row.get("wti_crude_oil")),
                nan_to_none(row.get("gold_price")),
                nan_to_none(row.get("usd_krw")),
                nan_to_none(row.get("us_10y_yield")),
                nan_to_none(row.get("fed_rate")),
                nan_to_none(row.get("kr_base_rate")),
            )
            for _, row in df.iterrows()
        ]

        execute_values(cur, """
            INSERT INTO market_global (
                trade_date,
                snp500_close, nasdaq_close, phlx_semi_close,
                vix, wti_crude_oil, gold_price,
                usd_krw, us_10y_yield, fed_rate, kr_base_rate
            ) VALUES %s
            ON CONFLICT (trade_date) DO UPDATE SET
                snp500_close    = COALESCE(EXCLUDED.snp500_close,    market_global.snp500_close),
                nasdaq_close    = COALESCE(EXCLUDED.nasdaq_close,    market_global.nasdaq_close),
                phlx_semi_close = COALESCE(EXCLUDED.phlx_semi_close, market_global.phlx_semi_close),
                vix             = COALESCE(EXCLUDED.vix,             market_global.vix),
                wti_crude_oil   = COALESCE(EXCLUDED.wti_crude_oil,   market_global.wti_crude_oil),
                gold_price      = COALESCE(EXCLUDED.gold_price,      market_global.gold_price),
                usd_krw         = COALESCE(EXCLUDED.usd_krw,         market_global.usd_krw),
                us_10y_yield    = COALESCE(EXCLUDED.us_10y_yield,    market_global.us_10y_yield),
                fed_rate        = COALESCE(EXCLUDED.fed_rate,        market_global.fed_rate),
                kr_base_rate    = COALESCE(EXCLUDED.kr_base_rate,    market_global.kr_base_rate)
        """, rows)
        conn.commit()

        # NaN 잔존 여부 확인
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE kr_base_rate IS NULL OR kr_base_rate::text = 'NaN')
            FROM market_global
        """)
        nan_left = cur.fetchone()[0]
        if nan_left > 0:
            logger.warning(f"kr_base_rate NaN/NULL 잔존: {nan_left}행")
        else:
            logger.info("kr_base_rate NaN 없음 ✅")

        logger.info(f"DB 저장 완료: {len(rows)}행 → market_global")

    except Exception as e:
        conn.rollback()
        logger.error(f"DB 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


def main():
    logger.info(f"===== 글로벌 데이터 초기 적재 ({START_DATE} ~ {END_DATE}) =====")
    df_yf   = fetch_yahoo()
    df_fred = fetch_fred()
    merge_and_save(df_yf, df_fred)
    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()