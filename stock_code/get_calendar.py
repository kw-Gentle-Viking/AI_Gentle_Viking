import pandas as pd
import holidays
import pandas_market_calendars as mcal
from pykrx import stock

def generate_full_calendar(start_year, end_year):
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"
    
    # 1. 기본 날짜 생성
    date_range = pd.date_range(start=start_date, end=end_date)
    df = pd.DataFrame(date_range, columns=['base_date'])
    df['day_of_week'] = df['base_date'].dt.dayofweek
    
    # 2. 휴일 명칭 가져오기 (holidays 라이브러리)
    kr_holiday_obj = holidays.KR(years=range(start_year, end_year + 1))
    us_holiday_obj = holidays.US(years=range(start_year, end_year + 1))
    
    # get() 함수를 쓰면 해당 날짜가 휴일이면 이름을, 아니면 None을 반환함
    df['kr_holiday_name'] = df['base_date'].apply(lambda x: kr_holiday_obj.get(x))
    df['us_holiday_name'] = df['base_date'].apply(lambda x: us_holiday_obj.get(x))
    
    # 3. 한국 영업일 체크 (pykrx) - 실제 장이 열렸던 날 기준
    print("한국 영업일 리스트 추출 중...")
    kr_business_days = []
    for year in range(start_year, end_year + 1):
        try:
            # 삼성전자 기준으로 실제 데이터가 존재하는 날짜 수집
            days = stock.get_market_ohlcv_by_date(f"{year}0101", f"{year}1231", "005930").index
            kr_business_days.extend(days.strftime('%Y-%m-%d').tolist())
        except: continue
    df['is_kr_business_day'] = df['base_date'].dt.strftime('%Y-%m-%d').isin(kr_business_days)
    
    # 4. 미국 영업일 체크 (NYSE 기준)
    print("미국 영업일 리스트 추출 중...")
    nyse = mcal.get_calendar('NYSE')
    us_schedule = nyse.schedule(start_date=start_date, end_date=end_date)
    us_business_days = us_schedule.index.strftime('%Y-%m-%d').tolist()
    df['is_us_business_day'] = df['base_date'].dt.strftime('%Y-%m-%d').isin(us_business_days)
    
    # 5. 결과 정리 (None 값 빈 문자열로 변경)
    df['kr_holiday_name'] = df['kr_holiday_name'].fillna("")
    df['us_holiday_name'] = df['us_holiday_name'].fillna("")
    
    # 6. CSV 저장
    filename = f"calendar_full_{start_year}_{end_year}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"완료! 파일명: {filename}")

# 실행
generate_full_calendar(2020, 2025)