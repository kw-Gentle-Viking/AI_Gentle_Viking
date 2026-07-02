"""
export_inference_results.py
============================
inference_results DB를 읽어 /home/user/inference_results.md 갱신
crontab: 평일 16:00 이후 (장 마감 후) 실행
"""

import os
import psycopg2
from datetime import datetime

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "0180"),
}

OUTPUT_PATH = "/home/user/inference_results.md"

TICKER_NAME = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
}


def fetch_daily_summary():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            trade_date,
            ticker,
            COUNT(*) FILTER (WHERE pred_str = '매수') AS buy_cnt,
            COUNT(*) FILTER (WHERE pred_str = '관망') AS hold_cnt,
            COUNT(*) FILTER (WHERE pred_str = '매도') AS sell_cnt,
            COUNT(*) AS total_cnt,
            (ARRAY_AGG(pred_str ORDER BY trade_datetime DESC))[1] AS last_signal,
            ROUND(AVG(prob_buy)::numeric, 3)  AS avg_buy,
            ROUND(AVG(prob_hold)::numeric, 3) AS avg_hold,
            ROUND(AVG(prob_sell)::numeric, 3) AS avg_sell
        FROM inference_results
        GROUP BY trade_date, ticker
        ORDER BY trade_date DESC, ticker
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def fetch_last_signals():
    """날짜별 × 종목별 마지막 5분봉 신호 상세"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (trade_date, ticker)
            trade_date, ticker, trade_datetime, pred_str,
            ROUND(prob_buy::numeric, 3),
            ROUND(prob_hold::numeric, 3),
            ROUND(prob_sell::numeric, 3)
        FROM inference_results
        ORDER BY trade_date DESC, ticker, trade_datetime DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def build_md(summary_rows, last_rows):
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("# 추론 결과 일별 요약")
    lines.append("")
    lines.append(f"> 마지막 갱신: {updated_at}  ")
    lines.append(f"> 기간: {summary_rows[-1][0]} ~ {summary_rows[0][0]}  ")
    total_days = len(set(r[0] for r in summary_rows))
    lines.append(f"> 총 {total_days} 영업일, {sum(r[5] for r in summary_rows):,}건")
    lines.append("")

    # ── 1. 일별 요약 테이블 ──────────────────────────────
    lines.append("## 1. 일별 신호 분포")
    lines.append("")
    lines.append("| 날짜 | 종목 | 매수 | 관망 | 매도 | 합계 | 장마감 신호 | 평균 매수확률 | 평균 매도확률 |")
    lines.append("|------|------|:----:|:----:|:----:|:----:|:-----------:|:-------------:|:-------------:|")

    for r in summary_rows:
        trade_date, ticker, buy_cnt, hold_cnt, sell_cnt, total_cnt, last_signal, avg_buy, avg_hold, avg_sell = r
        name = TICKER_NAME.get(ticker, ticker)
        halt_flag = " ⚠️" if last_signal == "거래정지" else ""
        avg_buy_str  = str(avg_buy)  if avg_buy  is not None else "-"
        avg_sell_str = str(avg_sell) if avg_sell is not None else "-"
        lines.append(
            f"| {trade_date} | {ticker} {name} "
            f"| {buy_cnt} | {hold_cnt} | {sell_cnt} | {total_cnt} "
            f"| **{last_signal}**{halt_flag} | {avg_buy_str} | {avg_sell_str} |"
        )

    lines.append("")

    # ── 2. 장마감 직전 신호 상세 ─────────────────────────
    lines.append("## 2. 장마감 직전 신호 상세")
    lines.append("")
    lines.append("| 날짜 | 종목 | 마지막 시각 | 신호 | 매수확률 | 관망확률 | 매도확률 |")
    lines.append("|------|------|:-----------:|:----:|:--------:|:--------:|:--------:|")

    for r in last_rows:
        trade_date, ticker, trade_dt, pred_str, pb, ph, ps = r
        name = TICKER_NAME.get(ticker, ticker)
        time_str = trade_dt.strftime("%H:%M") if trade_dt else "-"
        lines.append(
            f"| {trade_date} | {ticker} {name} | {time_str} "
            f"| **{pred_str}** | {pb} | {ph} | {ps} |"
        )

    lines.append("")
    return "\n".join(lines)


def main():
    summary_rows = fetch_daily_summary()
    last_rows = fetch_last_signals()

    if not summary_rows:
        print("추론 결과 없음")
        return

    md = build_md(summary_rows, last_rows)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {OUTPUT_PATH} 갱신 완료 ({len(summary_rows)}행)")


if __name__ == "__main__":
    main()
