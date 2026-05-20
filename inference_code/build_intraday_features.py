"""
build_intraday_features.py
==========================
추론용 장외 피처 생성 (매일 장 마감 후 실행)

추론용 서버 테이블:
    price_daily          → 일봉 기술적 피처
    investor_flow_daily  → 수급 피처
    daily_valuation      → PER/PBR
    market_global        → 글로벌 매크로
    market_index_daily   → KOSPI/KOSDAQ
    sector_daily_ohlcv   → 섹터 피처
    stock_events         → 종목 이벤트
    calendar             → 캘린더
    market_events        → 시장 이벤트
    ticker_metadata      → 종목 메타

출력:
    inference_features 테이블 (당일 피처)

crontab:
    30 16 * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/build_intraday_features.py >> /home/user/build_intraday_features.log 2>&1
"""

import os
import logging
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
import numpy as np
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("build_inference_features.log"),
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

TODAY    = date.today()
LOOKBACK = 300
FROM_DATE = TODAY - timedelta(days=LOOKBACK)

SECTOR_ID_TO_CODE = {
    0:  "0005", 1:  "0006", 2:  "0007", 3:  "0008",
    4:  "0009", 5:  "0010", 6:  "0011", 7:  "0012",
    8:  "0013", 9:  "0014", 10: "0015", 11: "0016",
    12: "0017", 13: "0018", 14: "0019", 15: "0020",
    16: "0021", 17: "0024", 18: "0025", 19: "0026",
}


def get_prev_trade_date() -> date:
    """price_daily에서 오늘 이전 최신 거래일 조회 (데이터 누수 방지)"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("""
        SELECT MAX(trade_date) FROM price_daily
        WHERE trade_date < %s
    """, (TODAY,))
    result = cur.fetchone()[0]
    cur.close()
    conn.close()
    return result if result else TODAY - timedelta(days=1)


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def nan_to_none(val):
    if val is None:
        return None
    try:
        import math
        if math.isnan(float(val)) or math.isinf(float(val)):
            return None
    except (TypeError, ValueError):
        pass
    return val


def init_table():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inference_features (
            ticker                  VARCHAR(10)  NOT NULL,
            trade_date              DATE         NOT NULL,
            log_ret_1d              DOUBLE PRECISION,
            disparity_5d            DOUBLE PRECISION,
            disparity_20d           DOUBLE PRECISION,
            disparity_60d           DOUBLE PRECISION,
            volatility_20d          DOUBLE PRECISION,
            prop_individual         DOUBLE PRECISION,
            prop_foreign            DOUBLE PRECISION,
            prop_institution        DOUBLE PRECISION,
            per                     DOUBLE PRECISION,
            pbr                     DOUBLE PRECISION,
            per_chg_1d              DOUBLE PRECISION,
            pbr_chg_1d              DOUBLE PRECISION,
            kospi_ret               DOUBLE PRECISION,
            kosdaq_ret              DOUBLE PRECISION,
            snp500_ret              DOUBLE PRECISION,
            nasdaq_ret              DOUBLE PRECISION,
            phlx_semi_ret           DOUBLE PRECISION,
            vix_chg                 DOUBLE PRECISION,
            usd_krw_chg             DOUBLE PRECISION,
            us_10y_yield_chg        DOUBLE PRECISION,
            rate_spread_us_kr       DOUBLE PRECISION,
            wti_ret                 DOUBLE PRECISION,
            gold_ret                DOUBLE PRECISION,
            sector_ret_1d           DOUBLE PRECISION,
            sector_ret_5d           DOUBLE PRECISION,
            sector_ret_20d          DOUBLE PRECISION,
            sector_ma_ratio_20d     DOUBLE PRECISION,
            sector_volatility       DOUBLE PRECISION,
            sector_volume_ratio     DOUBLE PRECISION,
            is_dividend             INT DEFAULT 0,
            is_bonus_issue          INT DEFAULT 0,
            is_rights_offering      INT DEFAULT 0,
            is_split                INT DEFAULT 0,
            is_merger               INT DEFAULT 0,
            is_earnings             INT DEFAULT 0,
            is_bok                  INT DEFAULT 0,
            is_fomc                 INT DEFAULT 0,
            is_witching_kr          INT DEFAULT 0,
            is_witching_us          INT DEFAULT 0,
            sector_id               INT,
            market_id               INT,
            day_of_week             INT,
            listing_days            INT,
            PRIMARY KEY (ticker, trade_date)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("테이블 초기화 완료")


def build_daily_tech() -> pd.DataFrame:
    logger.info("[1/7] 일봉 기술적 피처 계산 중...")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT ticker, trade_date, close_price
        FROM price_daily
        WHERE trade_date >= %s
        ORDER BY ticker, trade_date
    """, (FROM_DATE,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("price_daily 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["ticker", "trade_date", "close_price"])

    results = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        grp["close_price"] = grp["close_price"].ffill()

        grp["log_ret_1d"]    = np.log(grp["close_price"] / grp["close_price"].shift(1))
        grp["disparity_5d"]  = grp["close_price"] / grp["close_price"].rolling(5).mean()
        grp["disparity_20d"] = grp["close_price"] / grp["close_price"].rolling(20).mean()
        grp["disparity_60d"] = grp["close_price"] / grp["close_price"].rolling(60).mean()
        grp["volatility_20d"] = grp["log_ret_1d"].rolling(20).std()

        today_row = grp[grp["trade_date"] == PREV_DATE]
        if today_row.empty:
            continue
        results.append(today_row[[
            "ticker", "trade_date",
            "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d",
            "volatility_20d"
        ]])

    if not results:
        return pd.DataFrame()
    result_df = pd.concat(results, ignore_index=True)
    logger.info(f"  완료: {len(result_df)}종목")
    return result_df


def build_investor() -> pd.DataFrame:
    logger.info("[2/7] 수급 피처 계산 중...")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT ticker, trade_date,
               individual_net_amt, foreign_net_amt, inst_net_amt, market_cap
        FROM investor_flow_daily
        WHERE trade_date = %s
    """, (PREV_DATE,))
    rows = cur.fetchall()

    cur.execute("""
        SELECT ticker, market_cap FROM daily_valuation WHERE trade_date = %s
    """, (PREV_DATE,))
    val_rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("investor_flow_daily 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "ticker", "trade_date",
        "individual_net_amt", "foreign_net_amt", "inst_net_amt", "market_cap"
    ])

    df_val_mktcap = pd.DataFrame(val_rows, columns=["ticker", "market_cap_val"])
    df = df.merge(df_val_mktcap, on="ticker", how="left")
    df["market_cap_final"] = df["market_cap"].replace(0, np.nan).fillna(df["market_cap_val"])

    df["prop_individual"]  = df["individual_net_amt"] / df["market_cap_final"]
    df["prop_foreign"]     = df["foreign_net_amt"]    / df["market_cap_final"]
    df["prop_institution"] = df["inst_net_amt"]       / df["market_cap_final"]

    logger.info(f"  완료: {len(df)}종목")
    return df[["ticker", "trade_date", "prop_individual", "prop_foreign", "prop_institution"]]


def build_valuation() -> pd.DataFrame:
    logger.info("[3/7] 기업가치 피처 계산 중...")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT ticker, trade_date, per, pbr
        FROM daily_valuation
        WHERE trade_date >= %s
        ORDER BY ticker, trade_date
    """, (TODAY - timedelta(days=10),))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("daily_valuation 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["ticker", "trade_date", "per", "pbr"])
    df["per"] = df["per"].where(df["per"] > 0, None)
    df["pbr"] = df["pbr"].where(df["pbr"] > 0, None)

    results = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        grp["per"] = grp["per"].ffill()
        grp["pbr"] = grp["pbr"].ffill()
        grp["per_chg_1d"] = grp["per"].pct_change(fill_method=None)
        grp["pbr_chg_1d"] = grp["pbr"].pct_change(fill_method=None)

        today_row = grp[grp["trade_date"] == PREV_DATE]
        if today_row.empty:
            continue
        results.append(today_row[[
            "ticker", "trade_date",
            "per", "pbr", "per_chg_1d", "pbr_chg_1d"
        ]])

    if not results:
        return pd.DataFrame()
    result_df = pd.concat(results, ignore_index=True)
    logger.info(f"  완료: {len(result_df)}종목")
    return result_df


def build_market_macro() -> pd.DataFrame:
    logger.info("[4/7] 시장 매크로 피처 계산 중...")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT trade_date,
               snp500_close, nasdaq_close, phlx_semi_close,
               vix, usd_krw, us_10y_yield,
               wti_crude_oil, gold_price, fed_rate, kr_base_rate
        FROM market_global
        WHERE trade_date >= %s
        ORDER BY trade_date
    """, (TODAY - timedelta(days=10),))
    rows_global = cur.fetchall()

    cur.execute("""
        SELECT trade_date, close_price FROM market_index_daily
        WHERE index_code = '0001' AND trade_date >= %s ORDER BY trade_date
    """, (TODAY - timedelta(days=10),))
    kospi_rows = cur.fetchall()

    cur.execute("""
        SELECT trade_date, close_price FROM market_index_daily
        WHERE index_code = '1001' AND trade_date >= %s ORDER BY trade_date
    """, (TODAY - timedelta(days=10),))
    kosdaq_rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows_global:
        logger.warning("market_global 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows_global, columns=[
        "trade_date",
        "snp500_close", "nasdaq_close", "phlx_semi_close",
        "vix", "usd_krw", "us_10y_yield",
        "wti_crude_oil", "gold_price", "fed_rate", "kr_base_rate"
    ])

    for rows, col in [(kospi_rows, "kospi_close"), (kosdaq_rows, "kosdaq_close")]:
        if rows:
            tmp = pd.DataFrame(rows, columns=["trade_date", col])
            df  = df.merge(tmp, on="trade_date", how="left")

    df = df.sort_values("trade_date").reset_index(drop=True)

    for col in ["snp500_close","nasdaq_close","phlx_semi_close","vix","usd_krw",
                "us_10y_yield","wti_crude_oil","gold_price","fed_rate","kr_base_rate",
                "kospi_close","kosdaq_close"]:
        if col in df.columns:
            df[col] = df[col].ffill()

    df["kospi_ret"]         = df["kospi_close"].pct_change()
    df["kosdaq_ret"]        = df["kosdaq_close"].pct_change()
    df["snp500_ret"]        = df["snp500_close"].pct_change()
    df["nasdaq_ret"]        = df["nasdaq_close"].pct_change()
    df["phlx_semi_ret"]     = df["phlx_semi_close"].pct_change()
    df["vix_chg"]           = df["vix"].diff()
    df["usd_krw_chg"]       = df["usd_krw"].diff()
    df["us_10y_yield_chg"]  = df["us_10y_yield"].diff()
    df["rate_spread_us_kr"] = df["fed_rate"] - df["kr_base_rate"]
    df["wti_ret"]           = df["wti_crude_oil"].pct_change()
    df["gold_ret"]          = df["gold_price"].pct_change()

    today_row = df[df["trade_date"] == TODAY]  # 글로벌은 TODAY 기준
    if today_row.empty:
        logger.warning(f"오늘({TODAY}) market_global 데이터 없음")
        return pd.DataFrame()

    macro_cols = [
        "kospi_ret", "kosdaq_ret",
        "snp500_ret", "nasdaq_ret", "phlx_semi_ret",
        "vix_chg", "usd_krw_chg",
        "us_10y_yield_chg", "rate_spread_us_kr",
        "wti_ret", "gold_ret",
    ]
    result = today_row[macro_cols].iloc[0].to_dict()
    result["trade_date"] = TODAY
    logger.info("  완료")
    return pd.DataFrame([result])


def build_sector() -> pd.DataFrame:
    logger.info("[5/7] 섹터 피처 계산 중...")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT sector_code, trade_date, open, high, low, close, volume
        FROM sector_daily_ohlcv
        WHERE trade_date >= %s
        ORDER BY sector_code, trade_date
    """, (FROM_DATE,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("sector_daily_ohlcv 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "sector_code", "trade_date",
        "open", "high", "low", "close", "volume"
    ])

    results = []
    for sector_code, grp in df.groupby("sector_code"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        for col in ["open","high","low","close","volume"]:
            grp[col] = grp[col].ffill()

        grp["sector_ret_1d"]       = grp["close"].pct_change(1)
        grp["sector_ret_5d"]       = grp["close"].pct_change(5)
        grp["sector_ret_20d"]      = grp["close"].pct_change(20)
        grp["sector_ma_ratio_20d"] = grp["close"] / grp["close"].rolling(20).mean()
        grp["sector_volatility"]   = (grp["high"] - grp["low"]) / grp["close"]
        grp["tv_ma_20"]            = grp["volume"].rolling(20).mean()
        grp["sector_volume_ratio"] = grp["volume"] / grp["tv_ma_20"]

        today_row = grp[grp["trade_date"] == PREV_DATE]
        if today_row.empty:
            continue
        today_row = today_row.copy()
        today_row["sector_code"] = sector_code
        results.append(today_row[[
            "sector_code", "trade_date",
            "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
            "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio"
        ]])

    if not results:
        return pd.DataFrame()
    result_df = pd.concat(results, ignore_index=True)
    logger.info(f"  완료: {len(result_df)}섹터")
    return result_df


def build_event_calendar_static() -> pd.DataFrame:
    logger.info("[6/7] 이벤트/캘린더/Static 피처 수집 중...")
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT ticker, market_id, sector_id, listing_date
        FROM ticker_metadata
    """)
    ticker_rows = cur.fetchall()

    cur.execute("""
        SELECT ticker, event_type FROM stock_events WHERE event_date = %s
    """, (TODAY,))
    event_rows = cur.fetchall()

    cur.execute("""
        SELECT day_of_week FROM calendar WHERE base_date = %s
    """, (TODAY,))
    cal_row = cur.fetchone()

    cur.execute("""
        SELECT event_type FROM market_events WHERE event_date = %s
    """, (TODAY,))
    mkt_events = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()

    if not ticker_rows:
        logger.warning("ticker_metadata 없음")
        return pd.DataFrame()

    df = pd.DataFrame(ticker_rows, columns=[
        "ticker", "market_id", "sector_id", "listing_date"
    ])

    df["listing_date"] = pd.to_datetime(df["listing_date"]).dt.date
    df["listing_days"] = df["listing_date"].apply(
        lambda d: (TODAY - d).days if pd.notna(d) else None
    )

    event_map = {}
    for ticker, event_type in event_rows:
        event_map.setdefault(ticker, set()).add(event_type)

    def get_event(ticker, name):
        return 1 if name in event_map.get(ticker, set()) else 0

    df["is_dividend"]        = df["ticker"].apply(lambda t: get_event(t, "배당"))
    df["is_bonus_issue"]     = df["ticker"].apply(lambda t: get_event(t, "무상증자"))
    df["is_rights_offering"] = df["ticker"].apply(lambda t: get_event(t, "유상증자"))
    df["is_split"]           = df["ticker"].apply(lambda t: get_event(t, "액면분할"))
    df["is_merger"]          = df["ticker"].apply(lambda t: get_event(t, "합병"))
    df["is_earnings"]        = df["ticker"].apply(lambda t: get_event(t, "실적발표"))

    df["day_of_week"] = cal_row[0] if cal_row else TODAY.weekday()

    df["is_bok"]         = 1 if "BOK" in mkt_events else 0
    df["is_fomc"]        = 1 if "FOMC" in mkt_events else 0
    df["is_witching_kr"] = 1 if "WITCHING_KR" in mkt_events else 0
    df["is_witching_us"] = 1 if "WITCHING_US" in mkt_events else 0

    df["trade_date"] = TODAY
    logger.info(f"  완료: {len(df)}종목")
    return df[[
        "ticker", "trade_date",
        "market_id", "sector_id", "listing_days",
        "is_dividend", "is_bonus_issue", "is_rights_offering",
        "is_split", "is_merger", "is_earnings",
        "is_bok", "is_fomc", "is_witching_kr", "is_witching_us",
        "day_of_week",
    ]]


def save_features(df: pd.DataFrame):
    if df.empty:
        logger.warning("저장할 데이터 없음")
        return

    cols = [
        "ticker", "trade_date",
        "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d",
        "volatility_20d",
        "prop_individual", "prop_foreign", "prop_institution",
        "per", "pbr", "per_chg_1d", "pbr_chg_1d",
        "kospi_ret", "kosdaq_ret",
        "snp500_ret", "nasdaq_ret", "phlx_semi_ret",
        "vix_chg", "usd_krw_chg",
        "us_10y_yield_chg", "rate_spread_us_kr",
        "wti_ret", "gold_ret",
        "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
        "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio",
        "is_dividend", "is_bonus_issue", "is_rights_offering",
        "is_split", "is_merger", "is_earnings",
        "is_bok", "is_fomc", "is_witching_kr", "is_witching_us",
        "sector_id", "market_id",
        "day_of_week", "listing_days",
    ]

    for col in cols:
        if col not in df.columns:
            df[col] = None

    for col in cols:
        if col in df.columns and df[col].dtype in [float, 'float64']:
            df[col] = df[col].apply(nan_to_none)
    df = df.where(pd.notna(df), None)

    rows = [tuple(nan_to_none(row[col]) if col in df.columns else None for col in cols)
            for _, row in df.iterrows()]

    conn = get_conn()
    cur  = conn.cursor()
    try:
        execute_values(cur, f"""
            INSERT INTO inference_features ({', '.join(cols)})
            VALUES %s
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                {', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c not in ('ticker', 'trade_date'))}
        """, rows)
        conn.commit()
        logger.info(f"저장 완료: {len(rows)}종목 → inference_features")
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


def main():
    global PREV_DATE
    PREV_DATE = get_prev_trade_date()
    logger.info(f"===== 장외 피처 생성 시작 ({TODAY}) / 한국 데이터 기준일: {PREV_DATE} =====")

    # price_daily에 오늘 데이터 있는지 확인
    conn_chk = get_conn()
    cur_chk  = conn_chk.cursor()
    cur_chk.execute("SELECT COUNT(*) FROM price_daily WHERE trade_date = %s", (TODAY,))
    cnt = cur_chk.fetchone()[0]
    cur_chk.close()
    conn_chk.close()
    if cnt == 0:
        logger.warning(f"오늘({TODAY}) price_daily 데이터 없음 → 장 마감 후 재실행 필요")
        return

    # 주말 가드
    if TODAY.weekday() >= 5:
        logger.info(f"주말({TODAY}) → 건너뜀")
        return

    init_table()

    df_tech   = build_daily_tech()
    df_inv    = build_investor()
    df_val    = build_valuation()
    df_macro  = build_market_macro()
    df_sector = build_sector()
    df_event  = build_event_calendar_static()

    if df_event.empty:
        logger.error("ticker_metadata 없음, 종료")
        return

    df = df_event.copy()
    df["sector_code"] = df["sector_id"].map(SECTOR_ID_TO_CODE)

    if not df_tech.empty:
        df = df.merge(df_tech.drop(columns=["trade_date"]), on="ticker", how="left")
    if not df_inv.empty:
        df = df.merge(df_inv.drop(columns=["trade_date"]), on="ticker", how="left")
    if not df_val.empty:
        df = df.merge(df_val.drop(columns=["trade_date"]), on="ticker", how="left")
    if not df_macro.empty:
        for col in df_macro.columns:
            if col != "trade_date":
                df[col] = df_macro.iloc[0][col]
    if not df_sector.empty:
        df = df.merge(
            df_sector.drop(columns=["trade_date"]),
            on="sector_code", how="left"
        )

    df["trade_date"] = TODAY
    df = df[df["ticker"].str.len() == 6]
    df = df[~df["ticker"].str.contains("Z")]

    logger.info(f"[7/7] 전체 조인 완료: {len(df)}종목")
    save_features(df)
    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()