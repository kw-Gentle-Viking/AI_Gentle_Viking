# 시스템 동작 방식 정리

---

## 전체 구조

```
데이터 수집 → 피처 생성 → 추론
```

---

## 1. 데이터 수집

### crontab 설정

```
55 8  * * 1-5  collector_realtime.py        # 장중 while 루프 (08:55~15:30)
00 8  * * 1-5  collector_global.py          # Yahoo Finance + FRED
00 8  * * 1-5  collector_dart.py            # 공시 이벤트
00 16 * * 1-5  collector_batch.py           # 전 종목 1분봉/5분봉/일봉/섹터
50 16 * * 1-5  collector_daily_kis.py       # 수급/PER/PBR/시총/상장주식수
00 17 * * 1-5  build_inference_features.py  # 장외 피처 생성
*/5 *  * * 1-5  run_pipeline.py             # 5분봉 피처 생성 + 추론 (build_realtime_features → inference 순서 보장)
```

### `collector_batch.py` — 장 마감 후 배치 수집 (16:00)

- KOSDAQ150, KOSPI200 종목 CSV에서 ticker 목록 읽어옴
- KIS API로 당일 1분봉을 15:30부터 09:00까지 30분 단위 역순 호출
- 1분봉 → pandas resample로 5분봉 변환 (15:25 봉 제외)
- 1분봉 → `intraday_1min` (ON CONFLICT DO NOTHING)
- 5분봉 → `intraday_5min` (ON CONFLICT DO UPDATE)
- 섹터 일봉 → `sector_daily_ohlcv` (ON CONFLICT DO UPDATE)
- 종목 일봉 → `price_daily` (ON CONFLICT DO UPDATE)
  - `shares_outstanding` = 0 (API 미제공, collector_daily_kis.py에서 업데이트)
- `intraday_1min` 배치 후 삭제하지 않음 (collector_realtime과 공유)

### `collector_realtime.py` — 장중 실시간 수집 (08:55~15:30)

- 08:55 crontab → while 루프로 장 마감까지 동작
- 매 분 `60 - 현재초 + 5`초 대기 후 KIS API로 최신 1분봉 수집
- 1분봉 → `intraday_1min` (ON CONFLICT DO NOTHING)
- 봉의 분 % 5 == 4 일 때 `make_and_save_5min` 호출
  - `floor_5min()`으로 5분 구간 시작 시각 계산 (09:04 → 09:00)
  - 1분봉 1개 이상이면 불완전 봉도 저장
- 장 마감 후 `flush_incomplete_5min`으로 미완성 구간 강제 저장
- 15:25 봉 명시적 제외 (학습 데이터에 없음)
- 수집 대상: `REALTIME_TICKERS` (현재 005930, 000660)
- 주말 가드 있음

### `collector_global.py` — 글로벌 매크로 수집 (08:00)

- Yahoo Finance: S&P500, 나스닥, 필라델피아반도체, VIX, WTI, 금
- FRED: 원달러, 미국채10년, 연준금리, 한국기준금리(월별 → ffill)
- 미국 날짜 +1일 처리, 주말 데이터 제외
- NaN/inf → None 변환 후 저장
- ON CONFLICT DO UPDATE (COALESCE로 기존값 보존)

### `collector_daily_kis.py` — 한투 API 일별 수집 (16:50)

- 수급: 개인/외국인/기관 순매수 (당일치)
- PER/PBR/시가총액/상장주식수(lstn_stcn): 현재가 API (당일치)
- `price_daily.shares_outstanding` 당일 업데이트 (0인 경우에만)
- `investor_flow_daily.market_cap` 저장
- KOSPI/KOSDAQ 지수 종가

---

## 2. 확정된 피처 목록

### TFT 입력 분류 (코드 기준)

**Known Future (5개)** — 미래에도 알 수 있는 값
```
time_progress                          ← 5분봉
is_bok, is_fomc, is_witching_kr, is_witching_us  ← 장외
```

**Unknown Past (50개)** — 과거에만 알 수 있는 값
```
5분봉 (13개):
  rel_close, rel_high, rel_low, log_ret,
  disparity_5, disparity_20, disparity_60,
  vol_ratio, rsi_14, bb_position,
  macd_ratio, macd_signal_ratio, macd_hist_ratio

장외 (37개):
  log_ret_1d, disparity_5d, disparity_20d, disparity_60d, volatility_20d,
  prop_individual, prop_foreign, prop_institution,
  per, pbr, per_chg_1d, pbr_chg_1d,
  kospi_ret, kosdaq_ret, snp500_ret, nasdaq_ret, phlx_semi_ret,
  vix_chg, usd_krw_chg, us_10y_yield_chg, rate_spread_us_kr, wti_ret, gold_ret,
  sector_ret_1d, sector_ret_5d, sector_ret_20d,
  sector_ma_ratio_20d, sector_volatility, sector_volume_ratio,
  is_dividend, is_bonus_issue, is_rights_offering, is_split, is_merger, is_earnings,
  day_of_week, listing_days
```

**Static Categorical (2개)** — 시간에 따라 변하지 않는 값
```
sector_id, market_id
```

```
합계: 5 + 50 + 2 = 57개
```

### 장외 피처 (43개) — `inference_features`

| # | 피처명 | 그룹 | 수집처 | 수집 가능 |
|---|--------|------|--------|-----------|
| 1 | log_ret_1d | 일봉 기술적 | price_daily | ✅ |
| 2 | disparity_5d | 일봉 기술적 | price_daily | ✅ |
| 3 | disparity_20d | 일봉 기술적 | price_daily | ✅ |
| 4 | disparity_60d | 일봉 기술적 | price_daily | ✅ |
| 5 | volatility_20d | 일봉 기술적 | price_daily | ✅ |
| 6 | prop_individual | 수급 | investor_flow_daily | ⚠️ 4/6 이후만 |
| 7 | prop_foreign | 수급 | investor_flow_daily | ⚠️ 4/6 이후만 |
| 8 | prop_institution | 수급 | investor_flow_daily | ⚠️ 4/6 이후만 |
| 9 | per | 기업가치 | daily_valuation | ⚠️ 5/17 이후만 |
| 10 | pbr | 기업가치 | daily_valuation | ⚠️ 5/17 이후만 |
| 11 | per_chg_1d | 기업가치 | daily_valuation | ⚠️ 5/17 이후만 |
| 12 | pbr_chg_1d | 기업가치 | daily_valuation | ⚠️ 5/17 이후만 |
| 13 | kospi_ret | 매크로 | market_index_daily | ✅ |
| 14 | kosdaq_ret | 매크로 | market_index_daily | ✅ |
| 15 | snp500_ret | 매크로 | market_global | ✅ |
| 16 | nasdaq_ret | 매크로 | market_global | ✅ |
| 17 | phlx_semi_ret | 매크로 | market_global | ✅ |
| 18 | vix_chg | 매크로 | market_global | ✅ |
| 19 | usd_krw_chg | 매크로 | market_global | ✅ |
| 20 | us_10y_yield_chg | 매크로 | market_global | ✅ |
| 21 | rate_spread_us_kr | 매크로 | market_global | ✅ |
| 22 | wti_ret | 매크로 | market_global | ✅ |
| 23 | gold_ret | 매크로 | market_global | ✅ |
| 24 | sector_ret_1d | 섹터 | sector_daily_ohlcv | ✅ |
| 25 | sector_ret_5d | 섹터 | sector_daily_ohlcv | ✅ |
| 26 | sector_ret_20d | 섹터 | sector_daily_ohlcv | ✅ |
| 27 | sector_ma_ratio_20d | 섹터 | sector_daily_ohlcv | ✅ |
| 28 | sector_volatility | 섹터 | sector_daily_ohlcv | ✅ |
| 29 | sector_volume_ratio | 섹터 | sector_daily_ohlcv | ✅ |
| 30 | is_dividend | 종목이벤트 | stock_events | ✅ |
| 31 | is_bonus_issue | 종목이벤트 | stock_events | ✅ |
| 32 | is_rights_offering | 종목이벤트 | stock_events | ✅ |
| 33 | is_split | 종목이벤트 | stock_events | ✅ |
| 34 | is_merger | 종목이벤트 | stock_events | ✅ |
| 35 | is_earnings | 종목이벤트 | stock_events | ✅ |
| 36 | is_bok | Known Future | market_events | ✅ |
| 37 | is_fomc | Known Future | market_events | ✅ |
| 38 | is_witching_kr | Known Future | market_events | ✅ |
| 39 | is_witching_us | Known Future | market_events | ✅ |
| 40 | sector_id | Static | ticker_metadata | ✅ |
| 41 | market_id | Static | ticker_metadata | ✅ |
| 42 | day_of_week | 기타 | calendar | ✅ |
| 43 | listing_days | 기타 | ticker_metadata | ✅ |

### 5분봉 피처 (14개) — `realtime_features`

| # | 피처명 | 구분 |
|---|--------|------|
| 1 | time_progress | Known Future |
| 2 | rel_close | Unknown Past |
| 3 | rel_high | Unknown Past |
| 4 | rel_low | Unknown Past |
| 5 | log_ret | Unknown Past |
| 6 | disparity_5 | Unknown Past |
| 7 | disparity_20 | Unknown Past |
| 8 | disparity_60 | Unknown Past |
| 9 | vol_ratio | Unknown Past |
| 10 | rsi_14 | Unknown Past |
| 11 | bb_position | Unknown Past |
| 12 | macd_ratio | Unknown Past |
| 13 | macd_signal_ratio | Unknown Past |
| 14 | macd_hist_ratio | Unknown Past |

**제외 확정:**
- `vkospi_chg`: 한투/Yahoo 모두 수집 불가 → 완전 제거
- `is_short_selling_banned`: 사용 안 함 → 완전 제거
- `turnover_ratio`: market_cap 과거분 없음 → 완전 제거
- `is_kospi`, `is_kosdaq`: market_id로 대체 → 완전 제거

## 3. 피처 생성

### `init_inference_features.py` — 과거 전체 초기 적재 (1회)

- END_DATE = price_daily 최신 거래일 기준 (장 마감 전 실행 시 오늘 데이터 안 들어감)
- START_DATE(2026-01-01)~END_DATE 전체 raw 데이터 메모리 로드
- turnover_ratio = turnover / market_cap (daily_valuation 기준, ffill 적용)
- NaN/inf → None 변환 (nan_to_none)
- ON CONFLICT DO UPDATE

### `build_inference_features.py` — 매일 17:00 crontab

**데이터 누수 방지:**
- 한국 데이터(price_daily, investor_flow_daily, daily_valuation, sector_daily_ohlcv)는 PREV_DATE(전날 거래일) 기준
- 글로벌 데이터(market_global)는 TODAY 기준 (08:00 수집 = 전날 미국 데이터)
- 캘린더/이벤트는 TODAY 기준 (당일 알려진 정보)

- price_daily에 오늘 데이터 없으면 조기 종료
- turnover_ratio: shares_outstanding=0이면 daily_valuation.market_cap으로 대체
- 수급 market_cap: investor_flow_daily에 없으면 daily_valuation.market_cap으로 대체
- NaN/inf → None 변환 (nan_to_none)
- ON CONFLICT DO UPDATE

### `init_realtime_features.py` — 과거 전체 초기 적재 (1회)

- `intraday_5min` 종목별 전체 데이터 읽어 피처 계산
- ON CONFLICT DO NOTHING (재실행 시 TRUNCATE 필요)

### `build_realtime_features.py` — run_pipeline.py 통해 5분마다

- 오늘 -14일치 `intraday_5min` 로드 (disparity_60 워밍업)
- 장중(09:00~15:35)에만 실행, 주말 가드
- ON CONFLICT DO UPDATE

---

## 4. 추론

### `run_pipeline.py` — */5 * * * 1-5

- 장중: build_realtime_features → inference 순서 실행
- 장외: build_realtime_features 스킵, inference만 실행 (15:30 피처 기준)
- 주말 가드

### `inference.py` — run_pipeline.py 내부에서 호출 (직접 실행 X)

- 대상: REALTIME_TICKERS (005930, 000660, 추후 백엔드 연동)
- inference_features + realtime_features left join
- TFT 모델 추론 (encoder_length=60봉)
- NaN/None → 0으로 채워서 입력
- 결과 → `inference_results` (ON CONFLICT DO UPDATE)
- 주말 가드

---

## 5. 테이블 구조 요약

| 테이블 | 내용 | PK |
|---|---|---|
| `intraday_1min` | 1분봉 원본 | (ticker, datetime) |
| `intraday_5min` | 5분봉 원본 | (ticker, datetime) |
| `price_daily` | 일봉 원본 | (ticker, trade_date) |
| `sector_daily_ohlcv` | 섹터 일봉 | (sector_code, trade_date) |
| `investor_flow_daily` | 수급 | (ticker, trade_date) |
| `daily_valuation` | PER/PBR/시가총액 | (ticker, trade_date) |
| `market_global` | 글로벌 매크로 | (trade_date) |
| `market_index_daily` | KOSPI/KOSDAQ | (index_code, trade_date) |
| `stock_events` | 종목 이벤트 | - |
| `market_events` | 시장 이벤트 | - |
| `calendar` | 거래일 캘린더 | (base_date) |
| `ticker_metadata` | 종목 메타 | (ticker) |
| `inference_features` | 장외 피처 (45개) | (ticker, trade_date) |
| `realtime_features` | 5분봉 피처 (14개) | (ticker, trade_datetime) |
| `inference_results` | 추론 결과 | (ticker, trade_datetime) |

---

## 6. 알려진 한계 및 보완 방법

| 항목 | 현황 | 보완 방법 | 상태 |
|------|------|-----------|------|
| `vkospi_chg` | 수집 불가 | 피처 제거 완료 | ✅ 완료 |
| `is_short_selling_banned` | 사용 안 함 | 피처 제거 완료 | ✅ 완료 |
| `shares_outstanding` | 과거분 0 | collector_daily_kis.py에서 lstn_stcn으로 당일 업데이트 | 🕐 오늘 16:50 자동 해결 |
| `turnover_ratio` | 5/17 이전 NULL | daily_valuation.market_cap으로 대체, ffill | 🕐 매일 쌓이며 점진 보완 |
| `market_cap` (investor_flow) | 5/19 이전 없음 | daily_valuation.market_cap으로 대체 | 🕐 매일 쌓이며 점진 보완 |
| 수급 (prop_*) | 4/6 이전 NULL | API 30일 한계, 매일 수집으로 점진 보완 | 🕐 매일 쌓이며 점진 보완 |
| `per/pbr` | 5/17 이전 NULL | API 당일치 한계, 매일 수집으로 점진 보완 | 🕐 매일 쌓이며 점진 보완 |
| `kr_base_rate` | FRED 월별 지연 | ffill + DB 마지막 유효값 보완 (현재 2.5%) | ✅ 보완 완료 |
| 5분봉 과거 데이터 | 1분봉이 잘못 저장됨 | 외부 CSV 재수집 후 load_5min_csv.py로 적재 | 🔄 진행 중 |
| `init_realtime_features.py` | DO NOTHING이라 재실행 시 갱신 안됨 | TRUNCATE 후 재실행 필요 | ⚠️ 주의 |
| REALTIME_TICKERS | 하드코딩 | 추후 백엔드 연동으로 동적 수신 | 📌 TODO |
| `market_index_daily` 오늘 | 16:50 이전 없음 | 16:50 collector_daily_kis.py 실행 후 수집 | 🕐 오늘 16:50 자동 해결 |

**범례:**
- ✅ 완료
- 🕐 오늘 16:50 이후 자동 해결
- 🔄 진행 중
- ⚠️ 주의 필요
- 📌 추후 개발
