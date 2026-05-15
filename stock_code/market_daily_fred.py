import pandas_datareader.data as web
import pandas as pd
from datetime import datetime

def get_fred_indicators_raw(start_date="2019-12-31", end_date="2025-12-31"):
    """
    FRED(미국 연방준비은행) 데이터를 이용해 글로벌 지표 수집 (Raw 데이터 보존 버전)
    - VIXCLS: VIX 지수
    - DGS10: 미국 국채 10년물 금리
    - DEXKOUS: 원/달러 환율
    """
    # FRED 심볼 매핑
    symbols = {
        'VIXCLS': 'vix',
        'DGS10': 'us_10y_yield',
        'DEXKOUS': 'usd_krw'
    }

    print(f"🌍 FRED(연준) Raw 데이터 수집 시작: {start_date} ~ {end_date}")
    
    try:
        # 1. 데이터 가져오기
        df = web.DataReader(list(symbols.keys()), 'fred', start_date, end_date)
        
        # 2. 컬럼명 변경
        df.rename(columns=symbols, inplace=True)
        
        # 3. 데이터 후처리 (값 변경 없이 구조만 정리)
        df = df.reset_index()
        
        # FRED의 'DATE' 컬럼명을 설계하신 'trade_date'로 변경
        df.rename(columns={'DATE': 'trade_date'}, inplace=True)
        df['trade_date'] = df['trade_date'].dt.strftime('%Y-%m-%d')
        
        print(f"✅ 수집 완료: 총 {len(df)}행 (결측치 포함)")
        return df

    except Exception as e:
        print(f"❌ FRED 수집 중 오류 발생: {e}")
        return None

if __name__ == "__main__":
    global_df = get_fred_indicators_raw()
    
    if global_df is not None:
        # Raw 데이터이므로 결측치가 NaN으로 표시된 채 저장됩니다.
        global_df.to_csv("market_daily_fred.csv", index=False, encoding='utf-8-sig')
        print("\n--- 수집 데이터 하단 샘플 (NaN이 포함될 수 있음) ---")
        print(global_df.tail(10))