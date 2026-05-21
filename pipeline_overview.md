# 주식 데이터 수집 · 피처 생성 · 추론 파이프라인 정리

> **작성 기준**: 코드 내용 직접 분석 (파일명 주석이 실제 파일명과 다른 경우 코드 우선)  
> **DB**: PostgreSQL (`stock_db`)  
> **환경**: `conda activate kis_collector`

---

## 전체 파이프라인 흐름

```
[1회 초기 적재]
  init_calendar.py
  init_1day_data.py
  init_5min_data.py (CSV → DB)
  init_sector_data.py
  init_dart.py
  init_yf_fred.py
  init_kis.py
  init_realtime_features.py
  init_intraday_features.py
       ↓
[매일 반복 수집 — crontab]
  08:00  collector_dart.py      (공시 이벤트 최근 7일)
  08:00  collector_yf_fred.py   (글로벌 시장 데이터 최근 10일)
  08:55  collector_realtime.py  (장중 1분봉/5분봉 — 실시간 2종목)
  16:40  collector_batch.py     (전체 350종목 1분봉→5분봉, 섹터 일봉, 종목 일봉)
  16:50  collector_kis.py       (수급·PER/PBR·시총·지수)
       ↓
[매일 반복 피처 생성]
  */5분  build_realtime_features.py  (장중 5분봉 피처 upsert)
  17:00  build_intraday_features.py  (장외 종합 피처 → inference_features)
  17:10  build_batch_features.py     (전체 종목 5분봉 피처 배치)
       ↓
[추론]
  */5분  inference_pipeline.py  →  inference.py  (TFT 모델 추론 → inference_results)
       ↓
[검증 도구 (수동 실행)]
  validate_raw.py        validate_features.py
  check_db.py            check_db_dart.py
  check_db_kis.py        check_db_yf_fred.py
  check_db_realtime.py   check_db_features.py
```

---

## 1단계: 1회 초기 적재 (init_*.py)

### 1-1. `init_calendar.py`
- **목적**: `calendar` · `market_events` 테이블 초기 생성 (2020~2026년)
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `calendar` | `base_date` | `day_of_week`, `is_market_open`, `is_holiday`, `is_short_selling_banned` |
| `market_events` | `(event_date, event_type)` | `is_bok`, `is_fomc`, `is_witching_kr`, `is_witching_us` |

- **공매도 금지 기간** 하드코딩
  - 2020-03-16 ~ 2021-05-02 (코로나)
  - 2023-11-06 ~ 2099-12-31 (현재 금지 중)
- **이벤트 날짜 처리**
  - BOK: 한국 기준 그대로
  - FOMC: 미국 발표일 + 1일 (한국 반영일)
  - WITCHING_US: 미국 만기일 + 1일
- **`holidays` / `pandas_market_calendars`** 없으면 주말만 제외하는 폴백 로직 사용

---

### 1-2. `init_1day_data.py`
- **목적**: 종목별 일봉(OHLCV) 초기 수집 → `price_daily` 적재
- **API**: 한국투자증권 `FHKST03010100` (수정주가, 일봉)
- **수집 기간**: 코드 기준 `20260101 ~ 20260415` (설정값, 변경 가능)
- **종목 소스**: `KOSDAQ150_종목리스트.csv`, `KOSPI200_종목리스트.csv`
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `price_daily` | `(ticker, trade_date)` | `open_price`, `high_price`, `low_price`, `close_price`, `volume`, `turnover`, `shares_outstanding` |

- **연속조회**: `tr_cont = F/M`이면 재조회, `D/""` 이면 종료
- **거래량 0** 행 제외 (거래정지 대응)
- **Rate Limit**: `API_INTERVAL = 0.056초` (초당 18건)

---

### 1-3. `init_5min_data.py`
- **목적**: 대신증권 CYBOS Plus로 수집한 5분봉 CSV → `intraday_5min` 적재
- **CSV 위치**: `/home/user/KOSPI_A{ticker}_5min_data.csv`, `KOSDAQ_A{ticker}_5min_data.csv`
- **필터**
  - `datetime >= 2026-01-01`
  - 거래 시간 `09:00~15:30`
  - 거래량 > 0
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `intraday_5min` | `(ticker, datetime)` | `open`, `high`, `low`, `close`, `volume` (정수형) |

- `ON CONFLICT DO NOTHING` 방식으로 중복 방지

---

### 1-4. `init_sector_data.py`
- **목적**: 25개 섹터 일봉 OHLCV 초기 수집
- **API**: 한국투자증권 `FHKUP03500100` (섹터 지수 일봉)
- **수집 기간**: 코드 기준 `20260101 ~ 20260415`
- **섹터 소스**: `/home/user/국장_섹터리스트.csv` (컬럼: `sector_code`)
- **구현**: 45일씩 청크 분할 (API 1회 최대 50건 제한 대응)
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `sector_daily_ohlcv` | `(sector_code, trade_date)` | `open`, `high`, `low`, `close`, `volume` |

---

### 1-5. `init_dart.py`
- **목적**: OpenDartReader로 공시 이벤트 초기 수집 (`20260101 ~ 오늘`)
- **수집 이벤트 키워드 → 타입 매핑**

| 키워드 | 저장 타입 |
|---|---|
| 유상증자 | `유상증자` |
| 무상증자 | `무상증자` |
| 배당 | `배당` |
| 주식분할 | `액면분할` |
| 실적/잠정/매출액또는손익 | `실적발표` |
| 합병 | `합병` |

- `'Z'` 포함 ticker 제외 (우선주 등 필터)
- `suppress_stdout()` 컨텍스트 매니저로 OpenDartReader stdout 억제
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `stock_events` | `(ticker, event_date, event_type)` | `description` |

---

### 1-6. `init_yf_fred.py`
- **목적**: Yahoo Finance + FRED 글로벌 시장 데이터 초기 수집 (`20260101 ~ 오늘`)
- **Yahoo Finance 종목**

| 심볼 | 컬럼명 |
|---|---|
| `^GSPC` | `snp500_close` |
| `^IXIC` | `nasdaq_close` |
| `^SOX` | `phlx_semi_close` |
| `^VIX` | `vix` |
| `CL=F` | `wti_crude_oil` |
| `GC=F` | `gold_price` |

- **FRED 시리즈**

| FRED 코드 | 컬럼명 |
|---|---|
| `DEXKOUS` | `usd_krw` |
| `DGS10` | `us_10y_yield` |
| `DFEDTARL` | `fed_rate` |
| `INTDSRKRM193N` | `kr_base_rate` |

- **날짜 처리**: 미국 날짜 → 한국 날짜 (+1일, 데이터 누수 방지)
- **결측치**: ffill 후 주말 제거 / FRED `kr_base_rate`는 월별이므로 시작일 60일 전부터 조회
- **NaN 처리**: DB 저장 전 `None`으로 변환, `COALESCE`로 기존값 보존
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `market_global` | `trade_date` | `snp500_close`, `nasdaq_close`, `phlx_semi_close`, `vix`, `wti_crude_oil`, `gold_price`, `usd_krw`, `us_10y_yield`, `fed_rate`, `kr_base_rate` |

---

### 1-7. `init_kis.py`
- **목적**: 한국투자증권 API로 수급·PER/PBR·시총·지수 초기 수집
- **실제 파일명**: `init_kis.py` (코드 내부 주석 헤더는 `collector_daily_kis.py`로 되어 있으나, 이 파일이 `init_kis.py`임)
- **수집 항목**
  - `FHKST01010900`: 투자자별 매매동향 (개인/외국인/기관 순매수금액, 단위 × 1,000,000)
  - `FHKST01010100`: 현재가 시세 → PER, PBR, 시가총액(`hts_avls` × 1,000,000), 상장주식수(`lstn_stcn`)
  - `FHKUP03500100`: 지수 종가 (KOSPI `0001`, KOSDAQ `1001`, VKOSPI `101V1`)
- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `daily_valuation` | `(ticker, trade_date)` | `per`, `pbr`, `market_cap` |
| `investor_flow_daily` | `(ticker, trade_date)` | `individual_net_amt`, `foreign_net_amt`, `inst_net_amt`, `market_cap` |
| `market_index_daily` | `(index_code, trade_date)` | `close_price` |

- **추가 동작**: `price_daily.shares_outstanding`이 NULL/0인 행을 `lstn_stcn`으로 UPDATE

---

### 1-8. `init_realtime_features.py`
- **목적**: `intraday_5min` 전체 데이터 → `realtime_features` 초기 생성
- **처리 방식**: 종목별 전체 5분봉 로드 후 피처 계산
- **계산 피처** (14개)

| 피처 | 설명 |
|---|---|
| `time_progress` | 장 진행률 (0.0~1.0, 09:00 기준 390분) |
| `rel_close/high/low` | 당일 시가 대비 종가/고가/저가 비율 |
| `log_ret` | 로그 수익률 |
| `disparity_5/20/60` | 5/20/60봉 이동평균 대비 현재가 비율 |
| `vol_ratio` | 거래량 / 20봉 평균 거래량 |
| `rsi_14` | RSI(14) — EWM 방식 |
| `bb_position` | 볼린저밴드 위치 (0~1) |
| `macd_ratio` | MACD / 현재가 |
| `macd_signal_ratio` | MACD 시그널 / 현재가 |
| `macd_hist_ratio` | MACD 히스토그램 / 현재가 |

- **출력 테이블**

| 테이블 | PK |
|---|---|
| `realtime_features` | `(ticker, trade_datetime)` |

---

### 1-9. `init_intraday_features.py`
- **목적**: `inference_features` 테이블 초기 생성 (장외 종합 피처, 학습용 히스토리)
- **동작**: `build_intraday_features.py`와 동일한 로직
- **주의**: `PREV_DATE`는 `main()` 내에서 `global PREV_DATE`로 설정됨 (함수 바깥 선언 없음)
- **추론 파이프라인에서의 역할**: `inference.py`는 `inference_features`를 `trade_date = TODAY`만 조회하므로, 이 스크립트로 생성하는 히스토리 데이터는 **추론에 사용되지 않음** (학습 데이터 목적)

---

## 2단계: 매일 반복 수집 (collector_*.py)

### 2-1. `collector_dart.py`
- **crontab**: `00 08 * * 1-5`
- **수집 범위**: 최근 7일치 (`today - 7일 ~ today`)
- **로직**: `init_dart.py`와 동일한 키워드 매핑·필터 사용
- `ON CONFLICT DO NOTHING` → 중복 이벤트 무시

---

### 2-2. `collector_yf_fred.py`
- **crontab**: `00 08 * * 1-5`
- **수집 범위**: 최근 10일 (`today - 10일 ~ today`)
- **`kr_base_rate` 보완**: FRED에서 NaN이면 `market_global` DB에서 최근 유효값 가져와서 채움 (`get_last_valid()`)
- `COALESCE`로 기존값 우선 보존 (ON CONFLICT 시)
- **로직**: `init_yf_fred.py`와 동일

---

### 2-3. `collector_realtime.py`
- **crontab**: `55 08 * * 1-5`
- **대상 종목**: 하드코딩 `REALTIME_TICKERS = ["005930", "000660"]` (삼성전자, SK하이닉스)
- **동작 루프**
  1. 매 분 정각 + 5초 후 1분봉 조회 (`FHKST03010200`)
  2. `intraday_1min`에 저장
  3. 분봉 시각이 `X:04, X:09 ...` (5분 구간 마지막)이면 → `make_and_save_5min()` 호출
  4. 장 마감(`15:30`) 이후 → `flush_incomplete_5min()`으로 미완성 5분봉 저장 후 종료
- **5분봉 생성 규칙**
  - `15:25` 봉 제외 (학습 데이터와 맞추기 위해)
  - 불완전 봉 허용 (1분봉 1개 이상이면 저장)
- **토큰 갱신**: 23시간마다 자동 재발급
- **`intraday_1min`**: 공유 버퍼 역할, `collector_batch.py`와 함께 사용

---

### 2-4. `collector_batch.py`
- **crontab**: `40 16 * * 1-5`
- **역할**: 장 마감 후 전체 종목 일괄 수집
- **수집 흐름**
  1. **종목 1분봉 수집** (`FHKST03010200`): 15:30부터 09:00까지 30분씩 역순 조회, 1회 30봉 반환
  2. **1분봉 → 5분봉 리샘플**: `resample("5min", closed="left", label="left")`
     - 15:25 봉 제외 (학습 데이터와 맞추기 위해)
  3. **섹터 일봉 수집** (`FHKUP03500100`): 25개 섹터 코드 전체
  4. **종목 일봉 수집** (`FHKST03010100`): 수정주가, 당일 데이터만
- **DB 저장**
  - `intraday_1min`: `ON CONFLICT DO NOTHING`
  - `intraday_5min`: `ON CONFLICT DO UPDATE` (최신값으로 갱신)
  - `sector_daily_ohlcv`: `ON CONFLICT DO UPDATE`
  - `price_daily`: `ON CONFLICT DO UPDATE`
- **주의**: `shares_outstanding`은 FHKST03010100에서 제공 안 함 → `collector_kis.py`에서 별도 수집
- **`RESET_DB` 환경변수**: `true`로 설정 시 `intraday_1min`, `intraday_5min`, `sector_daily_ohlcv` 테이블 재생성

---

### 2-5. `collector_kis.py`
- **crontab**: `50 16 * * 1-5`
- **로직**: `init_kis.py`와 동일
- **주요 차이**: 오늘 날짜(`TODAY`)만 수집, crontab에서 매일 실행

---

### 2-6. `collector_temp.py`
- **용도**: 개발/테스트용 단순 실시간 수집기
- **특징**: DB 저장 없음, 메모리 버퍼(`dict[str, list]`)에만 저장
- **대상**: `["005930", "000660"]` 하드코딩
- **5분봉 생성**: 1분봉 5개 쌓일 때마다 resample (logger 출력만 함)

---

## 3단계: 피처 생성 (build_*.py)

### 3-1. `build_realtime_features.py`
- **crontab**: `*/5 09-15 * * 1-5` (장중 5분마다)
- **목적**: `intraday_5min` → `realtime_features` upsert
- **워밍업**: 과거 14일치 포함 로드 (disparity_60, MACD slow 26봉 확보용)
- **장 시간 가드**: `09:00 ~ 15:35` 외에는 실행 안 함
- **피처 목록**: `init_realtime_features.py`와 동일 (14개)
- **DB 동작**: `ON CONFLICT DO UPDATE SET` (기존 값 갱신)

---

### 3-2. `build_intraday_features.py`
- **crontab**: `00 17 * * 1-5`
- **목적**: 장외 종합 피처 → `inference_features` 생성
- **실행 가드**: `price_daily`에 오늘 데이터 없으면 종료 (장 마감 전 실행 방지)
- **`PREV_DATE`**: `price_daily`에서 오늘 이전 최신 거래일 조회 (데이터 누수 방지)
- **피처 생성 순서** (7단계)

| 단계 | 소스 테이블 | 피처 (개수) |
|---|---|---|
| 1/7 일봉 기술적 | `price_daily` | `log_ret_1d`, `disparity_5d/20d/60d`, `volatility_20d` (5개) |
| 2/7 수급 | `investor_flow_daily` | `prop_individual`, `prop_foreign`, `prop_institution` (3개) |
| 3/7 기업가치 | `daily_valuation` | `per`, `pbr`, `per_chg_1d`, `pbr_chg_1d` (4개) |
| 4/7 시장 매크로 | `market_global` + `market_index_daily` | `kospi_ret`, `kosdaq_ret`, `snp500_ret`, `nasdaq_ret`, `phlx_semi_ret`, `vix_chg`, `usd_krw_chg`, `us_10y_yield_chg`, `rate_spread_us_kr`, `wti_ret`, `gold_ret` (11개) |
| 5/7 섹터 | `sector_daily_ohlcv` | `sector_ret_1d/5d/20d`, `sector_ma_ratio_20d`, `sector_volatility`, `sector_volume_ratio` (6개) |
| 6/7 이벤트·캘린더·Static | `stock_events`, `calendar`, `market_events`, `ticker_metadata` | `is_dividend/bonus_issue/rights_offering/split/merger/earnings`, `is_bok/fomc/witching_kr/witching_us`, `sector_id`, `market_id`, `day_of_week`, `listing_days` (14개) |
| 7/7 조인 | 위 전체 | 종목(ticker) 기준 left join |

- **`SECTOR_ID_TO_CODE` 매핑**: `sector_id(0~19)` → `sector_code("0005"~"0026")` 변환용 딕셔너리
- **수급 비율 계산**: `prop_* = net_amt / market_cap_final` (market_cap이 0이면 daily_valuation 값 대체)
- **매크로 데이터**: 글로벌은 TODAY 기준 (이미 +1일 처리됨), 한국 일봉은 PREV_DATE 기준
- **출력 테이블**

| 테이블 | PK | 컬럼 수 |
|---|---|---|
| `inference_features` | `(ticker, trade_date)` | 44개 |

---

### 3-3. `build_batch_features.py`
- **crontab**: `10 17 * * 1-5`
- **목적**: 전체 종목 5분봉 피처 배치 생성 (REALTIME_TICKERS 포함)
- **워밍업**: 14일치 포함 로드
- **대상**: 오늘 `intraday_5min`에 데이터가 있는 종목 전체
- **피처 목록**: `build_realtime_features.py`와 동일 (14개)
- **DB 동작**: `ON CONFLICT DO UPDATE SET` (기존 값 갱신)
- **결과**: 오늘 봉만 추출해서 저장 (워밍업 데이터는 계산에만 사용)

---

## 4단계: 추론 (inference*.py)

### 4-1. `inference_pipeline.py`
- **crontab**: `*/5 * * * 1-5` (평일 매 5분)
- **목적**: `build_realtime_features.py` → `inference.py` 순서 보장 실행
- **실행 조건**: 장중(`09:00~15:30`)에만 실행
  - 장외에는 즉시 종료 — decoder step이 15:30봉이 되어 예측 대상(16:30)이 장 마감 후라 의미 없음
- **subprocess**로 각 스크립트 실행 (타임아웃 240초)

---

### 4-2. `inference.py`
- **실행 방식**: `inference_pipeline.py`에서 subprocess 호출 (crontab 직접 등록 없음)
- **모델**: TFT (Temporal Fusion Transformer) — `tft-torch` (PlaytikaOSS) v0.0.6
- **모델 경로**: `TFT_MODEL_PATH` 환경변수 (기본값: `/home/user/best_model_state_dict.pt`)
- **대상 종목**: `REALTIME_TICKERS = ["005930", "000660"]` (하드코딩)

#### 피처 로드 방식

| 피처 종류 | 조회 조건 | 설명 |
|---|---|---|
| `inference_features` | `trade_date = MAX(trade_date)` | **가장 최근 적재된 날짜** 사용 (장중에는 전일치, 16:30 이후에는 당일치 자동 전환) |
| `realtime_features` | `trade_date >= TODAY - 5일` | encoder 60봉 확보를 위해 최근 5일치 조회 |

- `realtime_features`를 5일치 조회한 뒤, **종목별 마지막 61봉(encoder 60 + decoder 1)만 사용** → 종목당 예측 샘플 정확히 1개

#### 피처 구성

| 구분 | 컬럼 출처 | 역할 |
|---|---|---|
| Known Future | `time_progress`, `is_bok`, `is_fomc`, `is_witching_kr`, `is_witching_us` | TFT known_future |
| Unknown Past | REALTIME_COLS + INFERENCE_COLS 나머지 | TFT time_varying_unknown |
| Static Categorical | `sector_id`, `market_id` | TFT static_categoricals (문자열 변환) |

- **TFT 설정**
  - `max_encoder_length = 60`
  - `max_prediction_length = 1`
  - `batch_size = 32`
- **추론 결과**: `pred_label` (0=매수, 1=관망, 2=매도), 각 클래스 확률

- **출력 테이블**

| 테이블 | PK | 주요 컬럼 |
|---|---|---|
| `inference_results` | `(ticker, trade_datetime)` | `pred_label`, `pred_str`, `prob_buy`, `prob_hold`, `prob_sell`, `model_version` |

---

## 5단계: 검증 도구 (validate_*.py / check_db*.py)

### 5-1. `validate_raw.py`
- **목적**: 피처 생성 **전** raw 테이블 품질 검증
- **검증 항목**

| 테이블 | 검증 내용 |
|---|---|
| `price_daily` | 날짜별 종목수, 비정상 종가, shares_outstanding |
| `investor_flow_daily` | 전부 0인 종목, market_cap 유무, 주말 데이터 |
| `daily_valuation` | NaN 잔존, market_cap 유무 |
| `market_global` | S&P, 환율, VIX, 연준금리, 한국기준금리 이상값 |
| `sector_daily_ohlcv` | 섹터수 (예상 25개), 비정상 종가 |
| 조인 키 | `ticker_metadata` ↔ `sector_daily_ohlcv` / `investor_flow_daily` |
| `market_index_daily` | 비정상값, 오늘 데이터 유무 |

- `EXPECTED_TICKERS = 349`, `EXPECTED_SECTORS = 25`
- 주말 데이터 자동 체크 (`EXTRACT(DOW FROM trade_date) IN (0, 6)`)

---

### 5-2. `validate_features.py`
- **목적**: 피처 생성 **후** 품질 검증
- **검증 대상**: `inference_features`, `realtime_features`
- **검증 항목**

| 구분 | 내용 |
|---|---|
| NULL/NaN 체크 | 컬럼별 허용 null 비율 설정 (per 30%, prop_* 10%, 나머지 0~1%) |
| 값 범위 체크 | `disparity_5d: 0.5~2.0`, `volatility_20d: 0~0.5`, `day_of_week: 0~6` 등 |
| 날짜별 종목수 | 예상 349개와 비교 |
| 섹터 조인 실패 | `sector_ret_1d`가 전부 없으면 오류 |

---

### 5-3. `check_db.py`
- **목적**: 전반적인 DB 수집 현황 확인 (수동 실행)
- **확인 항목**
  - 5분봉 날짜별 종목수·봉수
  - 1분봉 버퍼 현황
  - 오늘 수집 현황 (1분봉 있는데 5분봉 없는 종목)
  - 섹터 일봉 현황
  - 종목 일봉 현황
  - 5분봉 불완전 종목 (79봉 미만)

---

### 5-4. `check_db_dart.py`
- **목적**: `stock_events`, `calendar`, `market_events` 현황 확인
- **이벤트 타입별 집계**, 최근 7일 수집 현황, 오늘 수집 여부 확인

---

### 5-5. `check_db_kis.py`
- **목적**: `daily_valuation`, `investor_flow_daily`, `market_index_daily` 확인
- **오늘 현황 강조** (340종목 이상이면 정상으로 판단)

---

### 5-6. `check_db_yf_fred.py`
- **목적**: `market_global` 현황 확인
- **전체 NULL 현황**, 최근 5일 주요값 출력

---

### 5-7. `check_db_realtime.py`
- **목적**: 실시간 수집 종목 (`005930`, `000660`) 당일 5분봉 확인
- **확인 항목**: 1분봉·5분봉 건수, 최근 3개 봉 샘플, 마지막 저장 시각 (N분 전)

---

### 5-8. `check_db_features.py`
- **목적**: `inference_features`, `realtime_features`, `investor_flow_daily`, `daily_valuation`, `market_global`, `market_index_daily`, `ticker_metadata` 전체 상태 한 번에 확인
- **특징**: `check_nulls()` 함수로 모든 컬럼의 NULL/NaN/Inf 현황 자동 조회

---

## DB 테이블 전체 목록

| 테이블 | PK | 생성 위치 | 성격 |
|---|---|---|---|
| `calendar` | `base_date` | `init_calendar.py` | 1회 초기 |
| `market_events` | `(event_date, event_type)` | `init_calendar.py` | 1회 초기 |
| `price_daily` | `(ticker, trade_date)` | `init_1day_data.py`, `collector_batch.py` | 일봉 |
| `intraday_5min` | `(ticker, datetime)` | `init_5min_data.py`, `collector_batch.py`, `collector_realtime.py` | 5분봉 |
| `intraday_1min` | `(ticker, datetime)` | `collector_batch.py`, `collector_realtime.py` | 1분봉 버퍼 |
| `sector_daily_ohlcv` | `(sector_code, trade_date)` | `init_sector_data.py`, `collector_batch.py` | 섹터 일봉 |
| `stock_events` | `(ticker, event_date, event_type)` | `init_dart.py`, `collector_dart.py` | 공시 이벤트 |
| `market_global` | `trade_date` | `init_yf_fred.py`, `collector_yf_fred.py` | 글로벌 지수/매크로 |
| `daily_valuation` | `(ticker, trade_date)` | `init_kis.py`, `collector_kis.py` | PER/PBR/시총 |
| `investor_flow_daily` | `(ticker, trade_date)` | `init_kis.py`, `collector_kis.py` | 수급 |
| `market_index_daily` | `(index_code, trade_date)` | `init_kis.py`, `collector_kis.py` | KOSPI/KOSDAQ/VKOSPI |
| `realtime_features` | `(ticker, trade_datetime)` | `init_realtime_features.py`, `build_realtime_features.py`, `build_batch_features.py` | 5분봉 피처 |
| `inference_features` | `(ticker, trade_date)` | `init_intraday_features.py`, `build_intraday_features.py` | 장외 일봉 피처 |
| `inference_results` | `(ticker, trade_datetime)` | `inference.py` | 추론 결과 |
| `ticker_metadata` | `ticker` | (코드에서 생성 스크립트 없음, 별도 관리) | 종목 메타 |

---

## crontab 실제 설정

> `crontab -l` 기준 실제 등록된 내용 (코드 내 주석과 다른 경우 이쪽이 정확함)

### 환경변수 (crontab 상단에 설정)

```
KIS_APP_KEY=...
KIS_APP_SECRET=...
DB_HOST=localhost
DB_NAME=stock_db
DB_USER=stock_user
DB_PASSWORD=...
```

### 등록된 작업 목록

```cron
# 글로벌 데이터 + 공시 이벤트 (장 시작 전)
00 8  * * 1-5  collector_yf_fred.py   >> collector_yf_fred.log
00 8  * * 1-5  collector_dart.py      >> collector_dart.log

# 장중 실시간 수집 (005930, 000660 — 2종목)
55 8  * * 1-5  collector_realtime.py  >> realtime.log

# 수급·PER/PBR·지수 (장 마감 직전)
50 15 * * 1-5  collector_kis.py       >> collector_kis.log

# 장 마감 후 배치 수집 (전체 종목 1분봉→5분봉, 섹터·종목 일봉)
00 16 * * 1-5  collector_batch.py     >> batch.log

# 피처 생성 (배치 수집 완료 후)
30 16 * * 1-5  build_intraday_features.py  >> build_intraday_features.log
30 16 * * 1-5  build_batch_features.py     >> build_batch_features.log

# 5분봉 피처 생성 + 추론 파이프라인 (평일 매 5분, 17:30 이후 자동 종료)
*/5  * * * 1-5  inference_pipeline.py  >> pipeline.log
```

### 시각 순서 타임라인

| 실제 시각 | 스크립트 | 설명 |
|---|---|---|
| `00 08` | `collector_yf_fred.py` | 글로벌 시장 데이터 최근 10일 upsert |
| `00 08` | `collector_dart.py` | 공시 이벤트 최근 7일 upsert |
| `55 08` | `collector_realtime.py` | 장중 실시간 1분봉/5분봉 수집 시작 (2종목) |
| `*/5 (장중만 유효)` | `inference_pipeline.py` | 09:00~15:30 장중에만 실행 / 장외는 즉시 종료 |
| `50 15` | `collector_kis.py` | 수급·PER/PBR·시총·지수 수집 (**장 마감 직전**) |
| `00 16` | `collector_batch.py` | 전체 350종목 1분봉→5분봉, 섹터·종목 일봉 수집 |
| `30 16` | `build_intraday_features.py` | 장외 종합 피처 생성 → `inference_features` |
| `30 16` | `build_batch_features.py` | 전체 종목 5분봉 피처 배치 생성 → `realtime_features` |

> **주의**: `build_realtime_features.py`는 crontab에 직접 등록되지 않음 — `run_pipeline.py`가 장중에 subprocess로 호출함

### 코드 주석 vs 실제 crontab 차이점

| 스크립트 | 코드 내 주석 시각 | **실제 crontab 시각** |
|---|---|---|
| `collector_batch.py` | `40 16` | **`00 16`** |
| `collector_kis.py` | `50 16` | **`50 15`** |
| `build_intraday_features.py` | `00 17` | **`30 16`** |
| `build_batch_features.py` | `10 17` | **`30 16`** |

---

## 추론(Inference) 상세

### TFT 모델 개요

TFT(Temporal Fusion Transformer)는 **과거 N개의 시계열 스텝(encoder)을 읽고 다음 1개 스텝을 예측(decoder)** 하는 모델입니다. 이 파이프라인에서는 과거 60봉(5분봉 × 60 = 300분)을 encoder로 입력받아 **다음 5분봉 시점의 매수/관망/매도를 예측**합니다.

```
[encoder: 과거 60봉] ──▶ TFT ──▶ [decoder: 다음 1봉 예측]
                                    → pred_label: 0(매수) / 1(관망) / 2(매도)
                                    → prob_buy / prob_hold / prob_sell
```

---

### TFT 입력 3가지 분류

TFT는 입력을 세 종류로 구분합니다.

#### 1. Static Categoricals (2개) — 종목 고유 특성, 시간에 따라 변하지 않음

| 컬럼 | 출처 | 설명 |
|---|---|---|
| `sector_id` | `ticker_metadata` | 종목이 속한 섹터 ID (0~19, 문자열 변환 후 입력) |
| `market_id` | `ticker_metadata` | 시장 구분 (0=KOSDAQ, 1=KOSPI, 문자열 변환 후 입력) |

---

#### 2. Time-Varying Known Reals (5개) — 미래 시점 값도 사전에 알 수 있는 피처

encoder(과거)와 decoder(미래) 모두에 입력됩니다. 모델이 "앞으로 어떤 이벤트가 예정됐는지"를 알 수 있습니다.

| 컬럼 | 출처 | 설명 |
|---|---|---|
| `time_progress` | `realtime_features` | 장 진행률 (0.0=09:00, 1.0=15:30) — 다음 봉 시각은 미리 계산 가능 |
| `is_bok` | `inference_features` | 당일 한국은행 금통위 여부 |
| `is_fomc` | `inference_features` | 당일 FOMC 발표 영향일 여부 (미국 발표 +1일) |
| `is_witching_kr` | `inference_features` | 당일 한국 선물옵션 만기일 여부 |
| `is_witching_us` | `inference_features` | 당일 미국 선물옵션 만기 영향일 여부 (+1일) |

---

#### 3. Time-Varying Unknown Reals (49개) — 과거 봉에서만 관측 가능한 피처

encoder(과거)에만 입력됩니다. 5분봉 기술적 피처 13개 + 장외 컨텍스트 피처 36개로 구성됩니다.

**5분봉 기술적 피처 (realtime_features, 13개) — 봉마다 값이 다름**

| 컬럼 | 설명 |
|---|---|
| `rel_close` | 당일 시가 대비 현재가 비율 |
| `rel_high` | 당일 시가 대비 고가 비율 |
| `rel_low` | 당일 시가 대비 저가 비율 |
| `log_ret` | 5분봉 로그 수익률 |
| `disparity_5` | 현재가 / 5봉 이동평균 |
| `disparity_20` | 현재가 / 20봉 이동평균 |
| `disparity_60` | 현재가 / 60봉 이동평균 |
| `vol_ratio` | 거래량 / 20봉 평균 거래량 |
| `rsi_14` | RSI(14) |
| `bb_position` | 볼린저밴드 내 위치 (0~1) |
| `macd_ratio` | MACD / 현재가 |
| `macd_signal_ratio` | MACD 시그널 / 현재가 |
| `macd_hist_ratio` | MACD 히스토그램 / 현재가 |

**장외 컨텍스트 피처 (inference_features, 36개) — 하루 동안 고정, 모든 봉에 동일하게 broadcast**

| 그룹 | 컬럼 | 설명 |
|---|---|---|
| 일봉 기술적 | `log_ret_1d`, `disparity_5d`, `disparity_20d`, `disparity_60d`, `volatility_20d` | 전일 종가 기준 추세·변동성 |
| 수급 | `prop_individual`, `prop_foreign`, `prop_institution` | 개인·외국인·기관 순매수 / 시총 비율 |
| 밸류에이션 | `per`, `pbr`, `per_chg_1d`, `pbr_chg_1d` | PER·PBR 및 전일 대비 변화 |
| 국내 지수 | `kospi_ret`, `kosdaq_ret` | 당일 KOSPI·KOSDAQ 수익률 |
| 글로벌 지수 | `snp500_ret`, `nasdaq_ret`, `phlx_semi_ret` | S&P500·나스닥·필라델피아반도체 수익률 |
| 글로벌 매크로 | `vix_chg`, `usd_krw_chg`, `us_10y_yield_chg`, `rate_spread_us_kr`, `wti_ret`, `gold_ret` | VIX·환율·금리·원자재 변화 |
| 섹터 | `sector_ret_1d`, `sector_ret_5d`, `sector_ret_20d`, `sector_ma_ratio_20d`, `sector_volatility`, `sector_volume_ratio` | 소속 섹터의 수익률·추세·거래량 |
| 종목 이벤트 | `is_dividend`, `is_bonus_issue`, `is_rights_offering`, `is_split`, `is_merger`, `is_earnings` | 당일 공시 이벤트 여부 |
| 기타 | `day_of_week`, `listing_days` | 요일 (0=월~4=금), 상장 경과일 |

---

### 피처 결합 방식

```python
# realtime_features (시계열, 5일치) ← 5분봉 단위
# inference_features (고정값, 오늘 1행) ← ticker 기준 broadcast
df = df_rt.merge(df_inf.drop(columns=["trade_date"]), on="ticker", how="left")
```

각 5분봉 행에 오늘의 inference_features 값이 그대로 붙습니다. 즉 encoder 60봉 각각에는 "그 시점의 가격 행동(realtime) + 오늘의 시장 환경(inference)" 이 동시에 들어갑니다.

---

### 추론 타이밍과 데이터 준비 조건

| 시각 | 추론 실행 | 이유 |
|---|---|---|
| 09:00~15:30 (장중) | ✅ 5분마다 실행 | decoder step의 1시간 후가 장 마감 전 → 유효한 예측 |
| 15:30 이후 (장외) | ❌ 실행 안 함 | decoder step = 15:30봉, 예측 대상 = 16:30 (장 마감 후) → 무의미 |

---

### 현재 추론 준비 상태 점검 (2026-05-21 기준)

| 항목 | 상태 | 내용 |
|---|---|---|
| `realtime_features` (5일치) | ✅ | 005930: 309봉 / 000660: 309봉 (2026-05-15~05-20) |
| `inference_features` (최근) | ✅ | 2026-05-20 기준, 2종목 정상 적재 |
| `inference_results` 테이블 | ✅ 생성·데이터 있음 | 첫 실행으로 자동 생성, 2건 저장 완료 |
| `tft-torch` 패키지 | ✅ 설치완료 | v0.0.6 (kis_collector env) |
| 모델 가중치 파일 | ✅ 정상 동작 | `/home/user/best_model_state_dict.pt` |

**추론 실행 확인 결과 (2026-05-21 14:04)**
```
000660 | 2026-05-20 15:30:00 | 매수 | 매수:0.848 관망:0.055 매도:0.097
005930 | 2026-05-20 15:30:00 | 매수 | 매수:0.864 관망:0.057 매도:0.079
```

패키지와 가중치 파일 두 가지만 해결되면 데이터·코드 측면에서 추론은 즉시 동작 가능한 상태입니다.

---

## 수정 이력

### 2026-05-20 버그 수정

| # | 파일 | 수정 내용 |
|---|---|---|
| 1 | `init_intraday_features.py` | `build_daily_tech()` 내 `shares_outstanding` KeyError 제거 + `df_mktcap` dead code 제거 |
| 2 | `init_intraday_features.py` | `build_event_calendar_static()` 반환 목록에 `is_witching_us` 추가 |
| 3 | `inference.py` | `realtime_features` 조회를 `trade_date >= TODAY-5일`로 변경 (encoder 60봉 확보) |
| 4 | `inference.py` | 종목별 마지막 61봉 필터(`tail(ENCODER_LENGTH+1)`) 추가 → 결과 매핑 안정화 |
| 5 | `collector_batch.py` | `main()` 끝에 `clear_1min_buffer()` 호출 추가 (`intraday_1min` 매일 정리) |
| 6 | `inference_pipeline.py` | 17:30 이후 장외 실행 차단 (중복 추론 방지) |

---

## 주요 설계 결정 및 주의사항

### 날짜 처리
- **글로벌 데이터 (Yahoo Finance, FRED)**: 미국 기준 날짜 → +1일 변환하여 저장 (데이터 누수 방지)
- **한국 데이터 (KIS API, DART)**: 한국 기준 날짜 그대로 사용
- **피처 생성 시**: 글로벌은 TODAY 기준, 한국 일봉/수급은 PREV_DATE 기준 (장 마감 후 최신 거래일)

### 종목 필터
- ticker에 `'Z'` 포함 제외 (우선주 계열 필터)
- ticker 길이 6자리 필터

### NaN/None 처리
- DB에 `NaN` 저장 방지: 저장 전 항상 `nan_to_none()` 통과
- `ON CONFLICT DO UPDATE` 시 `COALESCE`로 기존 유효값 보존

### API Rate Limit
- KIS API: `API_INTERVAL = 0.056초` (초당 18건, 실전계좌 초당 20건 한도)
- 재시도: 최대 3회, 실패 시 2초 sleep

### SECTOR_ID_TO_CODE 매핑
- `ticker_metadata.sector_id` (0~19 정수) → `sector_daily_ohlcv.sector_code` (4자리 문자열) 변환
- 매핑: `{0:"0005", 1:"0006", ..., 17:"0024", 18:"0025", 19:"0026"}`
- 이 매핑은 `build_intraday_features.py`, `init_intraday_features.py`, `validate_raw.py`에서 동일하게 사용됨

### ticker_metadata 테이블
- 생성 스크립트가 현재 코드에 없음 — 별도로 관리 중
- 필요 컬럼: `ticker`, `is_kospi`, `is_kosdaq`, `market_id`, `sector_id`, `listing_date`
