import sys
import win32com.client
import pandas as pd
import time
import os

# =========================================================
# [설정]
# =========================================================
OUTPUT_DIR = "sector_index_daily"   # 결과 파일 저장 폴더
MASTER_FILE = "국장_섹터리스트.csv"   # 2단계에서 만든 섹터 마스터 파일

# [1] 실제 저장을 원하는 시작일
TARGET_SAVE_DATE_STR = "2020-01-01" 

# [2] 계산을 위해 미리 당겨서 수집할 시작일 (2019년 12월 20일)
FETCH_START_DATE_INT = 20191220 

# =========================================================
# 섹터 지수 데이터 수집 함수
# =========================================================
def get_sector_daily(sector_code, sector_name):
    # API 객체 생성
    obj = win32com.client.Dispatch("CpSysDib.StockChart")
    obj.SetInputValue(0, sector_code) 
    obj.SetInputValue(1, ord('1'))    # 기간 요청
    obj.SetInputValue(2, 20251231)    # 종료일 (미래)
    obj.SetInputValue(3, FETCH_START_DATE_INT)  # 시작일 (버퍼 포함)
    
    # 요청 필드: 날짜(0), 시가(2), 고가(3), 저가(4), 종가(5), 거래량(8), 거래대금(9)
    obj.SetInputValue(5, [0, 2, 3, 4, 5, 8, 9])
    obj.SetInputValue(6, ord('D'))    # 일간
    obj.SetInputValue(9, ord('1'))    # 수정주가
    
    data_list = []
    
    # 통신 연결 시도 (재시도 로직 추가 권장되나, 일단 원본 유지)
    retries = 3
    while retries > 0:
        obj.BlockRequest()
        status = obj.GetDibStatus()
        msg = obj.GetDibMsg1()

        if status != 0:
            print(f"⚠️ 통신 상태 확인 필요 ({msg}) - 재시도 중...")
            retries -= 1
            time.sleep(1)
            continue
        else:
            break
            
    if obj.GetDibStatus() != 0:
        print(f"❌ {sector_name}({sector_code}) 통신 실패")
        return pd.DataFrame()

    while True:
        count = obj.GetHeaderValue(3)
        if count == 0: break
        
        for i in range(count):
            data_list.append({
                'sector_code': sector_code, 
                'trade_date': str(obj.GetDataValue(0, i)),
                'open_price': obj.GetDataValue(1, i),
                'high_price': obj.GetDataValue(2, i),
                'low_price': obj.GetDataValue(3, i),
                'close_price': obj.GetDataValue(4, i),
                'volume': obj.GetDataValue(5, i),
                'trading_value': obj.GetDataValue(6, i)
            })
            
        if not obj.Continue: break
        obj.BlockRequest()
        time.sleep(0.2) # 과도한 요청 방지
        
    return pd.DataFrame(data_list)

# =========================================================
# 메인 실행
# =========================================================
def run_full_collection():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    
    if not os.path.exists(MASTER_FILE):
        print(f"❌ '{MASTER_FILE}' 파일이 없습니다.")
        return

    # [수정 1] 읽을 때부터 'sector_code'를 문자열(str)로 읽도록 강제
    master_df = pd.read_csv(MASTER_FILE, dtype={'sector_code': str})
    
    print(f"🚀 총 {len(master_df)}개 섹터 수집 시작")
    
    for idx, row in master_df.iterrows():
        # [수정 2] 혹시라도 1로 읽혔을 경우를 대비해 3자리 0 채우기 (001)
        clean_code = str(row['sector_code']).strip().zfill(3) 
        name = str(row['sector_name']).strip()
        
        # API 요청용 코드 생성 (001 -> U001)
        if not clean_code.startswith('U'):
            api_code = "U" + clean_code
        else:
            api_code = clean_code

        print(f"[{idx+1}/{len(master_df)}] {name} (API:{api_code} / SAVE:{clean_code}) 수집 중...", end=" ")
        
        try:
            df = get_sector_daily(api_code, name)
            
            if not df.empty:
                # 전처리
                df = df.sort_values('trade_date')
                df['change_rate'] = df['close_price'].pct_change() * 100
                df['change_rate'] = df['change_rate'].fillna(0).round(2)
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
                df = df[df['trade_date'] >= TARGET_SAVE_DATE_STR].copy()

                # 저장할 때는 표준 코드(001)로
                df['sector_code'] = clean_code # 위에서 이미 zfill(3) 처리함

                desired_cols = [
                    'sector_code', 'trade_date', 
                    'open_price', 'high_price', 'low_price', 'close_price', 
                    'volume', 'trading_value', 'change_rate'
                ]
                final_df = df[desired_cols]
                
                # 파일명 저장
                save_name = f"{clean_code}_sector_index_daily.csv"
                save_path = os.path.join(OUTPUT_DIR, save_name)
                
                final_df.to_csv(save_path, index=False, encoding='utf-8-sig')
                
                first_rate = final_df.iloc[0]['change_rate']
                print(f"✅ 완료 ({len(final_df)}건)")
                
            else:
                print("⚠️ 데이터 없음")
                
        except Exception as e:
            print(f"\n❌ 에러 발생: {e}")
            time.sleep(1)
            continue
            
    print(f"\n🎉 모든 수집 완료! '{OUTPUT_DIR}' 폴더를 확인하세요.")

if __name__ == "__main__":
    run_full_collection()