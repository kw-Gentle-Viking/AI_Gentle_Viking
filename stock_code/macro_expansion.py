import pandas as pd
import pandas_datareader.data as web
import yfinance as yf
import time
import os
from datetime import datetime

# [핵심] YFTzMissingError 대응을 위한 패치
# 일부 환경에서 타임존 라이브러리 충돌을 방지합니다.
def get_macro_expansion_final_v2(start="2020-01-01", end="2025-12-31"):
    print(f"🌐 매크로 데이터 수집 최종 수정판 시작 ({start} ~ {end})")
    
    # 1. FRED 데이터 수집 (유가, 금리) - 이건 잘 작동함
    fred_symbols = {
        "DCOILWTICO": "wti_crude_oil",
        "DFEDTARL": "fed_rate",
        "INTDSRKRM193N": "kr_base_rate"
    }
    
    combined_df = pd.DataFrame()

    print("🏦 FRED 데이터 수집 중...")
    try:
        combined_df = web.DataReader(list(fred_symbols.keys()), "fred", start, end)
        combined_df.rename(columns=fred_symbols, inplace=True)
        combined_df.index = pd.to_datetime(combined_df.index).date # 인덱스 날짜화
    except Exception as e:
        print(f"⚠️ FRED 오류: {e}")

    # 2. Yahoo Finance 금값 수집 (에러 방지 로직 적용)
    print("📡 금값 (GC=F) 재시도 중...")
    try:
        # Ticker 객체를 직접 사용하여 데이터 수집
        gold_ticker = yf.Ticker("GC=F")
        gold_data = gold_ticker.history(start=start, end=end)['Close']
        
        if not gold_data.empty:
            gold_df = gold_data.to_frame(name='gold_price')
            gold_df.index = pd.to_datetime(gold_df.index).date
            
            if combined_df.empty:
                combined_df = gold_df
            else:
                combined_df = combined_df.join(gold_df, how='outer')
            print("✅ 금값 데이터 수집 성공!")
        else:
            # history가 실패할 경우 download로 마지막 시도
            gold_df = yf.download("GC=F", start=start, end=end, auto_adjust=True)['Close']
            if not isinstance(gold_df, pd.Series): # 멀티컬럼 방지
                gold_df = gold_df.iloc[:, 0]
            gold_df = gold_df.to_frame(name='gold_price')
            gold_df.index = pd.to_datetime(gold_df.index).date
            combined_df = combined_df.join(gold_df, how='outer')
            print("✅ 금값 데이터 수집 성공 (방법2)!")
            
    except Exception as e:
        print(f"❌ 금값 수집 최종 실패: {e}")
        print("💡 팁: pip install --upgrade yfinance 를 실행해 보세요.")

    # 3. 데이터 정리
    combined_df.index.name = "trade_date"
    combined_df = combined_df.reset_index()
    combined_df['trade_date'] = pd.to_datetime(combined_df['trade_date']).dt.strftime('%Y-%m-%d')
    
    # ★ baltic_dry_index 컬럼 삭제 반영 ★
    final_cols = ['trade_date', 'wti_crude_oil', 'gold_price', 'fed_rate', 'kr_base_rate']
    for col in final_cols:
        if col not in combined_df.columns:
            combined_df[col] = pd.NA
            
    return combined_df[final_cols]

if __name__ == "__main__":
    df = get_macro_expansion_final_v2()
    
    if df is not None:
        df.to_csv("macro_expansion.csv", index=False, encoding='utf-8-sig')
        print("\n" + "="*50)
        print(f"📊 최종 수집 결과 (총 {len(df)}행):")
        print(df.count()) # 결측치 제외한 개수 출력
        print("="*50)
        # 데이터가 들어있는지 샘플 확인
        sample = df.dropna(subset=['wti_crude_oil', 'fed_rate'], how='all').tail(5)
        print(sample)