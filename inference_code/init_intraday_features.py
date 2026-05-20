"""
init_inference_features.py
==========================
추론용 장외 피처 초기 적재 (1회 실행)
2026-01-01 ~ 오늘까지 날짜별로 피처 생성

실행:
    conda activate kis_collector
    python ~/init_inference_features.py
"""

import os
import sys
import logging
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
import numpy as np
from datetime import date, timedelta
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("init_inference_features.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정값
# ============================================================
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

START_DATE = date(2026, 1, 1)
# END_DATE는 price_daily 최신 거래일 기준 (오늘이 장 마감 전이면 어제까지)
# main()에서 DB 조회 후 결정
LOOKBACK   = 300  # 피처 계산용 과거 데이터 기간

SECTOR_ID_TO_CODE = {
    0:  "0005", 1:  "0006", 2:  "0007", 3:  "0008",
    4:  "0009", 5:  "0010", 6:  "0011", 7:  "0012",
    8:  "0013", 9:  "0014", 10: "0015", 11: "0016",
    12: "0017", 13: "0018", 14: "0019", 15: "0020",
    16: "0021", 17: "0024", 18: "0025", 19: "0026",
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def nan_to_none(val):
    """NaN, inf → None 변환 (DB에 NaN 저장 방지)"""
    if val is None:
        return None
    try:
        import math
        if math.isnan(float(val)) or math.isinf(float(val)):
            return None
    except (TypeError, ValueError):
        pass
    return val


# ============================================================
# 1. 전체 데이터 로드 (한 번만)
# ============================================================
def load_all_data():
    logger.info("전체 데이터 로드 중...")
    conn = get_conn()
    cur  = conn.cursor()
    from_date = START_DATE - timedelta(days=LOOKBACK)

    # 일봉
    cur.execute("""
        SELECT ticker, trade_date, close_price, turnover, shares_outstanding
        FROM price_daily WHERE trade_date >= %s ORDER BY ticker, trade_date
    """, (from_date,))
    df_price = pd.DataFrame(cur.fetchall(), columns=[
        "ticker", "trade_date", "close_price", "turnover", "shares_outstanding"
    ])

    # 수급
    cur.execute("""
        SELECT ticker, trade_date, individual_net_amt, foreign_net_amt,
               inst_net_amt, market_cap
        FROM investor_flow_daily WHERE trade_date >= %s ORDER BY ticker, trade_date
    """, (from_date,))
    df_flow = pd.DataFrame(cur.fetchall(), columns=[
        "ticker", "trade_date",
        "individual_net_amt", "foreign_net_amt", "inst_net_amt", "market_cap"
    ])

    # PER/PBR
    cur.execute("""
        SELECT ticker, trade_date, per, pbr, market_cap
        FROM daily_valuation WHERE trade_date >= %s ORDER BY ticker, trade_date
    """, (from_date,))
    df_val = pd.DataFrame(cur.fetchall(), columns=["ticker", "trade_date", "per", "pbr", "market_cap"])

    # 글로벌 매크로
    cur.execute("""
        SELECT trade_date, snp500_close, nasdaq_close, phlx_semi_close,
               vix, usd_krw, us_10y_yield, wti_crude_oil, gold_price,
               fed_rate, kr_base_rate
        FROM market_global WHERE trade_date >= %s ORDER BY trade_date
    """, (from_date,))
    df_global = pd.DataFrame(cur.fetchall(), columns=[
        "trade_date", "snp500_close", "nasdaq_close", "phlx_semi_close",
        "vix", "usd_krw", "us_10y_yield", "wti_crude_oil", "gold_price",
        "fed_rate", "kr_base_rate"
    ])

    # 지수
    for code, col in [("0001", "kospi_close"), ("1001", "kosdaq_close"), ("101V1", "vkospi")]:
        cur.execute("""
            SELECT trade_date, close_price FROM market_index_daily
            WHERE index_code = %s AND trade_date >= %s ORDER BY trade_date
        """, (code, from_date))
        tmp = pd.DataFrame(cur.fetchall(), columns=["trade_date", col])
        df_global = df_global.merge(tmp, on="trade_date", how="left")

    # 섹터
    cur.execute("""
        SELECT sector_code, trade_date, open, high, low, close, volume
        FROM sector_daily_ohlcv WHERE trade_date >= %s ORDER BY sector_code, trade_date
    """, (from_date,))
    df_sector = pd.DataFrame(cur.fetchall(), columns=[
        "sector_code", "trade_date", "open", "high", "low", "close", "volume"
    ])

    # 종목 이벤트
    cur.execute("""
        SELECT ticker, event_date, event_type FROM stock_events
        WHERE event_date >= %s
    """, (START_DATE,))
    df_events = pd.DataFrame(cur.fetchall(), columns=["ticker", "event_date", "event_type"])

    # 캘린더
    cur.execute("""
        SELECT base_date, day_of_week, is_short_selling_banned
        FROM calendar WHERE base_date >= %s
    """, (START_DATE,))
    df_cal = pd.DataFrame(cur.fetchall(), columns=[
        "base_date", "day_of_week", "is_short_selling_banned"
    ])

    # 시장 이벤트
    cur.execute("""
        SELECT event_date, event_type FROM market_events
        WHERE event_date >= %s
    """, (START_DATE,))
    df_mkt_events = pd.DataFrame(cur.fetchall(), columns=["event_date", "event_type"])

    # 종목 메타
    cur.execute("""
        SELECT ticker, market_id, sector_id, listing_date FROM ticker_metadata
    """)
    df_meta = pd.DataFrame(cur.fetchall(), columns=[
        "ticker", "market_id", "sector_id", "listing_date"
    ])

    cur.close()
    conn.close()
    logger.info("데이터 로드 완료")
    return df_price, df_flow, df_val, df_global, df_sector, df_events, df_cal, df_mkt_events, df_meta


# ============================================================
# 2. 일봉 기술적 피처 사전 계산
# ============================================================
def precompute_daily_tech(df_price: pd.DataFrame, df_val: pd.DataFrame) -> pd.DataFrame:
    logger.info("일봉 기술적 피처 사전 계산 중...")
    # daily_valuation market_cap (shares_outstanding=0 대체용)
    df_mktcap = df_val[["ticker", "trade_date", "market_cap"]].copy()
    df_mktcap = df_mktcap[df_mktcap["market_cap"].notna() & (df_mktcap["market_cap"] > 0)]
    results = []
    for ticker, grp in df_price.groupby("ticker"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        grp["close_price"]        = grp["close_price"].ffill()
        grp["turnover"]           = grp["turnover"].ffill()
        grp["shares_outstanding"] = grp["shares_outstanding"].ffill()

        grp["log_ret_1d"]    = np.log(grp["close_price"] / grp["close_price"].shift(1))
        grp["disparity_5d"]  = grp["close_price"] / grp["close_price"].rolling(5).mean()
        grp["disparity_20d"] = grp["close_price"] / grp["close_price"].rolling(20).mean()
        grp["disparity_60d"] = grp["close_price"] / grp["close_price"].rolling(60).mean()
        grp["market_cap_shares"] = grp["close_price"] * grp["shares_outstanding"]
        # daily_valuation market_cap으로 대체
        val_ticker = df_mktcap[df_mktcap["ticker"] == ticker][["trade_date", "market_cap"]]
        val_ticker = val_ticker.rename(columns={"market_cap": "market_cap_val"})
        grp = grp.merge(val_ticker, on="trade_date", how="left")
        grp["market_cap"] = grp["market_cap_shares"].replace(0, np.nan).fillna(grp["market_cap_val"])
        # market_cap ffill (당일 없으면 전일값으로 채움)
        grp["market_cap"] = grp["market_cap"].ffill()
        grp["turnover_ratio"] = grp["turnover"] / grp["market_cap"].replace(0, np.nan)
        grp["volatility_20d"]= grp["log_ret_1d"].rolling(20).std()
        grp["ticker"] = ticker
        results.append(grp[[
            "ticker", "trade_date",
            "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d",
            "turnover_ratio", "volatility_20d"
        ]])
    return pd.concat(results, ignore_index=True)


# ============================================================
# 3. 수급 피처 사전 계산
# ============================================================
def precompute_investor(df_flow: pd.DataFrame, df_val: pd.DataFrame) -> pd.DataFrame:
    logger.info("수급 피처 사전 계산 중...")
    df = df_flow.copy()
    # market_cap: investor_flow_daily 우선, 없으면 daily_valuation에서 가져옴
    df_mktcap = df_val[["ticker", "trade_date", "market_cap"]].copy()
    df_mktcap = df_mktcap[df_mktcap["market_cap"].notna() & (df_mktcap["market_cap"] > 0)]
    df = df.merge(df_mktcap.rename(columns={"market_cap": "market_cap_val"}),
                  on=["ticker", "trade_date"], how="left")
    df["market_cap_final"] = df["market_cap"].replace(0, np.nan).fillna(df["market_cap_val"])
    # market_cap_final이 없으면 종목 내 ffill
    df = df.sort_values(["ticker", "trade_date"])
    df["market_cap_final"] = df.groupby("ticker")["market_cap_final"].ffill()
    df["prop_individual"]  = df["individual_net_amt"] / df["market_cap_final"]
    df["prop_foreign"]     = df["foreign_net_amt"]    / df["market_cap_final"]
    df["prop_institution"] = df["inst_net_amt"]       / df["market_cap_final"]
    return df[["ticker", "trade_date", "prop_individual", "prop_foreign", "prop_institution"]]


# ============================================================
# 4. 기업가치 피처 사전 계산
# ============================================================
def precompute_valuation(df_val: pd.DataFrame) -> pd.DataFrame:
    logger.info("기업가치 피처 사전 계산 중...")
    results = []
    for ticker, grp in df_val.groupby("ticker"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        grp["per"] = grp["per"].where(grp["per"] > 0, None).ffill()
        grp["pbr"] = grp["pbr"].where(grp["pbr"] > 0, None).ffill()
        grp["per_chg_1d"] = grp["per"].pct_change(fill_method=None)
        grp["pbr_chg_1d"] = grp["pbr"].pct_change(fill_method=None)
        grp["ticker"] = ticker
        results.append(grp[["ticker", "trade_date", "per", "pbr", "per_chg_1d", "pbr_chg_1d"]])
    if not results:
        return pd.DataFrame()
    return pd.concat(results, ignore_index=True)


# ============================================================
# 5. 시장 매크로 피처 사전 계산
# ============================================================
def precompute_macro(df_global: pd.DataFrame) -> pd.DataFrame:
    logger.info("시장 매크로 피처 사전 계산 중...")
    df = df_global.sort_values("trade_date").reset_index(drop=True)
    for col in ["snp500_close", "nasdaq_close", "phlx_semi_close", "vix",
                "usd_krw", "us_10y_yield", "wti_crude_oil", "gold_price",
                "fed_rate", "kr_base_rate", "kospi_close", "kosdaq_close", "vkospi"]:
        if col in df.columns:
            df[col] = df[col].ffill()

    df["kospi_ret"]        = df["kospi_close"].pct_change()
    df["kosdaq_ret"]       = df["kosdaq_close"].pct_change()
    df["snp500_ret"]       = df["snp500_close"].pct_change()
    df["nasdaq_ret"]       = df["nasdaq_close"].pct_change()
    df["phlx_semi_ret"]    = df["phlx_semi_close"].pct_change()
    df["vix_chg"]          = df["vix"].diff()
    df["usd_krw_chg"]      = df["usd_krw"].diff()
    df["us_10y_yield_chg"] = df["us_10y_yield"].diff()
    df["rate_spread_us_kr"]= df["fed_rate"] - df["kr_base_rate"]
    df["wti_ret"]          = df["wti_crude_oil"].pct_change()
    df["gold_ret"]         = df["gold_price"].pct_change()

    return df[["trade_date", "kospi_ret", "kosdaq_ret", "snp500_ret", "nasdaq_ret",
               "phlx_semi_ret", "vix_chg", "usd_krw_chg",
               "us_10y_yield_chg", "rate_spread_us_kr", "wti_ret", "gold_ret"]]


# ============================================================
# 6. 섹터 피처 사전 계산
# ============================================================
def precompute_sector(df_sector: pd.DataFrame) -> pd.DataFrame:
    logger.info("섹터 피처 사전 계산 중...")
    results = []
    for sector_code, grp in df_sector.groupby("sector_code"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            grp[col] = grp[col].ffill()

        grp["sector_ret_1d"]       = grp["close"].pct_change(1)
        grp["sector_ret_5d"]       = grp["close"].pct_change(5)
        grp["sector_ret_20d"]      = grp["close"].pct_change(20)
        grp["sector_ma_ratio_20d"] = grp["close"] / grp["close"].rolling(20).mean()
        grp["sector_volatility"]   = (grp["high"] - grp["low"]) / grp["close"]
        grp["tv_ma_20"]            = grp["volume"].rolling(20).mean()
        grp["sector_volume_ratio"] = grp["volume"] / grp["tv_ma_20"]
        grp["sector_code"] = sector_code
        results.append(grp[[
            "sector_code", "trade_date",
            "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
            "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio"
        ]])
    return pd.concat(results, ignore_index=True)


# ============================================================
# 7. 날짜별 피처 조립 및 저장
# ============================================================
def build_and_save_for_date(
    target_date, df_tech, df_inv, df_val_feat, df_macro,
    df_sector_feat, df_events, df_cal, df_mkt_events, df_meta
):
    # 종목 메타 (기준)
    df = df_meta.copy()
    df["trade_date"]  = target_date
    df["sector_code"] = df["sector_id"].map(SECTOR_ID_TO_CODE)
    df["listing_date"]= pd.to_datetime(df["listing_date"]).dt.date
    df["listing_days"]= df["listing_date"].apply(
        lambda d: (target_date - d).days if pd.notna(d) else None
    )

    # 일봉 기술적
    day_tech = df_tech[df_tech["trade_date"] == target_date]
    if not day_tech.empty:
        df = df.merge(day_tech.drop(columns=["trade_date"]), on="ticker", how="left")

    # 수급
    day_inv = df_inv[df_inv["trade_date"] == target_date]
    if not day_inv.empty:
        df = df.merge(day_inv.drop(columns=["trade_date"]), on="ticker", how="left")

    # 기업가치
    if not df_val_feat.empty:
        day_val = df_val_feat[df_val_feat["trade_date"] == target_date]
        if not day_val.empty:
            df = df.merge(day_val.drop(columns=["trade_date"]), on="ticker", how="left")

    # 매크로
    day_macro = df_macro[df_macro["trade_date"] == target_date]
    if not day_macro.empty:
        for col in day_macro.columns:
            if col != "trade_date":
                df[col] = day_macro.iloc[0][col]

    # 섹터
    day_sector = df_sector_feat[df_sector_feat["trade_date"] == target_date]
    if not day_sector.empty:
        df = df.merge(day_sector.drop(columns=["trade_date"]), on="sector_code", how="left")

    # 이벤트
    day_events = df_events[df_events["event_date"] == target_date]
    event_map  = {}
    for _, row in day_events.iterrows():
        event_map.setdefault(row["ticker"], set()).add(row["event_type"])

    def get_event(ticker, name):
        return 1 if name in event_map.get(ticker, set()) else 0

    df["is_dividend"]        = df["ticker"].apply(lambda t: get_event(t, "배당"))
    df["is_bonus_issue"]     = df["ticker"].apply(lambda t: get_event(t, "무상증자"))
    df["is_rights_offering"] = df["ticker"].apply(lambda t: get_event(t, "유상증자"))
    df["is_split"]           = df["ticker"].apply(lambda t: get_event(t, "액면분할"))
    df["is_merger"]          = df["ticker"].apply(lambda t: get_event(t, "합병"))
    df["is_earnings"]        = df["ticker"].apply(lambda t: get_event(t, "실적발표"))

    # 캘린더
    cal_row = df_cal[df_cal["base_date"] == target_date]
    df["day_of_week"]             = cal_row.iloc[0]["day_of_week"]             if not cal_row.empty else target_date.weekday()
    df["is_short_selling_banned"] = int(cal_row.iloc[0]["is_short_selling_banned"]) if not cal_row.empty else 0

    # 시장 이벤트
    mkt = df_mkt_events[df_mkt_events["event_date"] == target_date]["event_type"].tolist()
    df["is_bok"]         = 1 if "BOK" in mkt else 0
    df["is_fomc"]        = 1 if "FOMC" in mkt else 0
    df["is_witching_kr"] = 1 if "WITCHING_KR" in mkt else 0
    df["is_witching_us"] = 1 if "WITCHING_US" in mkt else 0

    df = df[df["ticker"].str.len() == 6]
    df = df[~df["ticker"].str.contains("Z")]
    df = df.where(pd.notna(df), None)

    return df


def save_to_db(conn, df: pd.DataFrame):
    if df.empty:
        return 0
    cols = [
        "ticker", "trade_date",
        "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d",
        "volatility_20d",
        "prop_individual", "prop_foreign", "prop_institution",
        "per", "pbr", "per_chg_1d", "pbr_chg_1d",
        "kospi_ret", "kosdaq_ret", "snp500_ret", "nasdaq_ret", "phlx_semi_ret",
        "vix_chg", "usd_krw_chg", "us_10y_yield_chg",
        "rate_spread_us_kr", "wti_ret", "gold_ret",
        "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
        "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio",
        "is_dividend", "is_bonus_issue", "is_rights_offering",
        "is_split", "is_merger", "is_earnings",
        "is_short_selling_banned", "is_bok", "is_fomc",
        "is_witching_kr", "is_witching_us",
        "sector_id", "market_id", "day_of_week", "listing_days",
    ]
    for col in cols:
        if col not in df.columns:
            df[col] = None

    rows = [tuple(row[col] for col in cols) for _, row in df.iterrows()]
    cur = conn.cursor()
    try:
        execute_values(cur, f"""
            INSERT INTO inference_features ({', '.join(cols)})
            VALUES %s
            ON CONFLICT (ticker, trade_date) DO NOTHING
        """, rows)
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
        return 0
    finally:
        cur.close()


# ============================================================
# 메인
# ============================================================
def main():
    global END_DATE
    # price_daily 최신 거래일을 END_DATE로 사용 (장 마감 전 오늘 데이터 없으면 어제까지)
    conn_tmp = get_conn()
    cur_tmp  = conn_tmp.cursor()
    cur_tmp.execute("SELECT MAX(trade_date) FROM price_daily")
    END_DATE = cur_tmp.fetchone()[0]
    cur_tmp.close()
    conn_tmp.close()
    logger.info(f"===== 장외 피처 초기 적재 시작 ({START_DATE} ~ {END_DATE}) =====")

    # 테이블 생성 (build_inference_features.py와 동일 구조)
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
            turnover_ratio          DOUBLE PRECISION,
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
            is_short_selling_banned INT DEFAULT 0,
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
    logger.info("테이블 생성 완료")

    # 전체 데이터 한 번에 로드
    df_price, df_flow, df_val, df_global, df_sector, df_events, df_cal, df_mkt_events, df_meta = load_all_data()

    # 사전 계산
    df_tech       = precompute_daily_tech(df_price, df_val)
    df_inv        = precompute_investor(df_flow, df_val)
    df_val_feat   = precompute_valuation(df_val)
    df_macro      = precompute_macro(df_global)
    df_sector_feat= precompute_sector(df_sector)

    # 날짜 타입 통일
    for df in [df_tech, df_inv, df_val_feat, df_macro, df_sector_feat]:
        if not df.empty and "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df_events["event_date"]     = pd.to_datetime(df_events["event_date"]).dt.date
    df_cal["base_date"]         = pd.to_datetime(df_cal["base_date"]).dt.date
    df_mkt_events["event_date"] = pd.to_datetime(df_mkt_events["event_date"]).dt.date

    # 한국 거래일 목록 (price_daily 기준)
    trade_dates = sorted(
        df_price[(df_price["trade_date"] >= START_DATE) &
                 (df_price["trade_date"] <= END_DATE)]["trade_date"].unique()
    )
    logger.info(f"처리할 거래일: {len(trade_dates)}일")

    # 날짜별 피처 생성 및 저장
    conn = get_conn()
    total_saved = 0

    for target_date in tqdm(trade_dates, desc="날짜별 피처 생성"):
        df = build_and_save_for_date(
            target_date, df_tech, df_inv, df_val_feat, df_macro,
            df_sector_feat, df_events, df_cal, df_mkt_events, df_meta
        )
        saved = save_to_db(conn, df)
        total_saved += saved

    conn.close()
    logger.info(f"===== 완료: 총 {total_saved:,}행 저장 =====")


if __name__ == "__main__":
    main()