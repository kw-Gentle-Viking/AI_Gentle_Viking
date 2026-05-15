import yfinance as yf
import pandas as pd
import os

# =========================================================
# [설정] 수집 기간 및 저장 파일명
# =========================================================
START_DATE = "2019-12-31"
END_DATE = "2025-12-31"
OUTPUT_FILENAME = "market_daily_yf.csv"

def get_us_market_indices():
    print(f"🌍 미국 주요 지수(S&P500, NASDAQ, SOX) 수집 시작: {START_DATE} ~ {END_DATE}")
    
    # 1. 야후 파이낸스 티커 정의
    # ^GSPC: S&P 500
    # ^IXIC: 나스닥 종합지수 (Nasdaq Composite)
    # ^SOX : 필라델피아 반도체 지수
    tickers = ['^GSPC', '^IXIC', '^SOX']
    
    try:
        # 2. 데이터 다운로드 (종가 기준)
        # progress=False: 지저분한 로딩바 제거
        df = yf.download(tickers, start=START_DATE, end=END_DATE, progress=False)['Close']
        
        if df.empty:
            print("❌ 데이터를 가져오지 못했습니다.")
            return None

        # 3. 컬럼 이름 변경 (DB 컬럼명과 매핑)
        # yfinance는 알파벳 순서로 컬럼을 정렬해서 줄 때가 있으므로 딕셔너리로 명확히 매핑
        df = df.rename(columns={
            '^GSPC': 'snp500_close',
            '^IXIC': 'nasdaq_close',
            '^SOX': 'phlx_semi_close'
        })
        
        # 4. 데이터 정리
        df = df.reset_index() # Date 인덱스를 컬럼으로
        
        # 날짜 포맷 통일 (YYYY-MM-DD)
        # Yahoo 데이터는 Timezone 정보가 있을 수 있어 제거(tz_localize) 후 포맷팅
        df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
        df['trade_date'] = df['Date'].dt.strftime('%Y-%m-%d')
        
        # 불필요한 'Date' 컬럼 제거 및 컬럼 순서 정리
        df = df[['trade_date', 'snp500_close', 'nasdaq_close', 'phlx_semi_close']]
        
        # 날짜순 정렬
        df = df.sort_values('trade_date')
        
        # 소수점 둘째자리 반올림 (선택사항, 지수는 보통 소수점 2자리 관리)
        cols = ['snp500_close', 'nasdaq_close', 'phlx_semi_close']
        df[cols] = df[cols].round(2)

        print(f"✅ 수집 완료: 총 {len(df)}거래일")
        return df

    except Exception as e:
        print(f"❌ 수집 중 오류 발생: {e}")
        return None

if __name__ == "__main__":
    df_result = get_us_market_indices()
    
    if df_result is not None:
        # CSV 저장
        df_result.to_csv(OUTPUT_FILENAME, index=False, encoding='utf-8-sig')
        print(f"\n📂 저장 완료: {OUTPUT_FILENAME}")
        print(df_result.tail()) # 끝부분 확인