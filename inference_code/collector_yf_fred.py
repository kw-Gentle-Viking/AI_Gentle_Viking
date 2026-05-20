"""
collector_yf_fred.py
====================
글로벌 시장 데이터 매일 자동 수집 (Yahoo Finance + FRED)
- 최근 7일치 수집 후 DB upsert
- 매일 오전 8시 crontab 실행

수집 항목:
    Yahoo Finance: S&P500, 나스닥, 필라델피아반도체, VIX, WTI, 금
    FRED: 원달러, 미국채10년, 연준금리, 한국기준금리

주의:
    - 미국 날짜 → 한국 날짜 +1일 처리 (데이터 누수 방지)
    - FRED kr_base_rate는 월별 업데이트 → ffill로 채움
    - NaN은 DB 저장 전 None으로 변환

crontab:
    00 08 * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/collector_global.py >> /home/user/global.log 2>&1
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
        logging.FileHandler("collector_global.log"),
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

END_DATE   = date.today()
START_DATE = END_DATE - timedelta(days=10)  # 여유있게 10일치

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
    """NaN, inf → None 변환"""
    if val is None:
        return None
    try:
        if np.isnan(val) or np.isinf(val):
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
            # 미국 날짜 → 한국 날짜 (+1일)
            df.index = (df.index + timedelta(days=1)).date
            df.index.name = "trade_date"
            # ffill (주말/공휴일 결측치)
            df = df.ffill()
            # 여전히 NaN인 것 확인
            nan_counts = df.isna().sum()
            if nan_counts.any():
                logger.warning(f"Yahoo NaN 존재: {nan_counts[nan_counts > 0].to_dict()}")
            logger.info(f"Yahoo Finance 수집 완료: {len(df)}행")
            return df
        except Exception as e:
            logger.warning(f"Yahoo Finance 실패 (attempt {attempt+1}): {e}")
            time.sleep(3)
    logger.error("Yahoo Finance 수집 최종 실패")
    return pd.DataFrame()


def fetch_fred() -> pd.DataFrame:
    logger.info("FRED 수집 시작...")
    for attempt in range(3):
        try:
            df = web.DataReader(
                list(FRED_TICKERS.keys()), "fred",
                (START_DATE - timedelta(days=60)).strftime("%Y-%m-%d"),  # kr_base_rate 월별이라 여유있게
                END_DATE.strftime("%Y-%m-%d"),
            )
            df = df.rename(columns=FRED_TICKERS)
            df.index = pd.to_datetime(df.index)
            # ffill (FRED 주말/공휴일 + 월별 업데이트 kr_base_rate)
            df = df.ffill()
            # 미국 날짜 → 한국 날짜 (+1일)
            df.index = (df.index + timedelta(days=1)).date
            df.index.name = "trade_date"
            # 최근 날짜만 필터링
            df = df[df.index >= START_DATE]
            nan_counts = df.isna().sum()
            if nan_counts.any():
                logger.warning(f"FRED NaN 존재: {nan_counts[nan_counts > 0].to_dict()}")
            logger.info(f"FRED 수집 완료: {len(df)}행")
            return df
        except Exception as e:
            logger.warning(f"FRED 실패 (attempt {attempt+1}): {e}")
            time.sleep(3)
    logger.error("FRED 수집 최종 실패")
    return pd.DataFrame()


def get_last_valid(col: str) -> float | None:
    """DB에서 해당 컬럼의 최근 유효값 가져오기 (NaN/NULL 제외)"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT {col} FROM market_global
            WHERE {col} IS NOT NULL
            AND {col}::text != 'NaN'
            ORDER BY trade_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        return None


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

    # NaN → None 변환 (DB에 NaN 저장 방지)
    for col in ALL_COLS:
        df[col] = df[col].apply(nan_to_none)

    # kr_base_rate NaN이면 DB에서 마지막 유효값으로 채우기
    last_kr = get_last_valid("kr_base_rate")
    if last_kr is not None:
        df["kr_base_rate"] = df["kr_base_rate"].apply(
            lambda x: x if x is not None else last_kr
        )
        logger.info(f"kr_base_rate 보완: {last_kr}")

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    try:
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

        # 저장 결과 확인
        cur.execute("""
            SELECT trade_date,
                   snp500_close, usd_krw, vix, kr_base_rate
            FROM market_global
            ORDER BY trade_date DESC LIMIT 5
        """)
        logger.info("최근 5일 저장 현황:")
        for r in cur.fetchall():
            logger.info(f"  {r[0]} | S&P={r[1]} | USD/KRW={r[2]} | VIX={r[3]} | KR금리={r[4]}")

        logger.info(f"DB 저장 완료: {len(rows)}행 → market_global")
    except Exception as e:
        conn.rollback()
        logger.error(f"DB 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


def main():
    logger.info(f"===== 글로벌 데이터 수집 시작 ({START_DATE} ~ {END_DATE}) =====")
    df_yf   = fetch_yahoo()
    df_fred = fetch_fred()
    merge_and_save(df_yf, df_fred)
    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()