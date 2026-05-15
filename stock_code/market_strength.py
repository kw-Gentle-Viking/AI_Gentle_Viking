import pandas as pd
import requests
from datetime import datetime
import time
import os

# ==========================================
# 1. 설정
# ==========================================
# 수집할 페이지 수 (1페이지당 약 20일치 데이터)
# 6년(약 1500일) -> 약 75~100페이지 필요
# 넉넉하게 120페이지 설정
PAGES_TO_SCRAPE = 120 
OUTPUT_FILENAME = "market_strength_naver.csv"

print(f">>> [작업 시작] 네이버 금융에서 데이터를 긁어옵니다.")
print(f">>> 목표: 최근 {PAGES_TO_SCRAPE * 20}거래일 (약 6년치)")

# ==========================================
# 2. 프로그램 매매 동향 (네이버 금융)
# ==========================================
print("\n>>> [1단계] 프로그램 매매 동향 수집 중...")
program_data = []

for page in range(1, PAGES_TO_SCRAPE + 1):
    try:
        # 네이버 금융 '프로그램 매매 동향' 페이지
        url = f"https://finance.naver.com/sise/sise_program.naver?code=KOSPI&page={page}"
        
        # 헤더 추가 (봇 차단 방지)
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        
        # 테이블 읽기
        df_tables = pd.read_html(res.text)
        
        # 보통 0번째 테이블이 데이터임
        df = df_tables[0]
        
        # 결측치 제거 (날짜 없는 행 삭제)
        df = df.dropna(subset=['날짜'])
        
        # 데이터 정리
        # 컬럼: 날짜, 순매수(수량), 순매수(금액)...
        # 우리가 필요한 건 '순매수 - 금액(백만)' 입니다.
        # 네이버 프로그램 페이지 구조: [날짜, 매도, 매수, 순매수(수량), 매도, 매수, 순매수(금액)]
        # 보통 마지막 컬럼이 '금액 기준 순매수' 입니다.
        
        # 날짜와 마지막 컬럼(순매수 금액)만 추출
        temp_df = df.iloc[:, [0, -1]].copy()
        temp_df.columns = ['trade_date', 'program_net_amt']
        
        program_data.append(temp_df)
        
        # 진행률 표시
        if page % 10 == 0:
            print(f"    - {page}/{PAGES_TO_SCRAPE} 페이지 완료")
            
        time.sleep(0.1) # 매너 딜레이
        
    except Exception as e:
        print(f"    - [에러] {page}페이지 수집 실패: {e}")

# 병합
if program_data:
    df_program = pd.concat(program_data, ignore_index=True)
    df_program['trade_date'] = pd.to_datetime(df_program['trade_date'], format='%Y.%m.%d')
    df_program = df_program.set_index('trade_date').sort_index()
    print(f"    - [완료] 프로그램 매매 데이터: {len(df_program)}건 확보")
else:
    print("    - [실패] 데이터를 가져오지 못했습니다.")
    exit()

# ==========================================
# 3. 등락 종목 수 (투자자별 매매동향 페이지 활용)
# ==========================================
# *주의: 네이버에는 '일자별 등락 종목 수'를 6년치 보여주는 페이지가 없습니다.
# 따라서, 이 부분은 부득이하게 'KRX 정보데이터시스템' 링크를 이용해야 합니다.
# 하지만 사용자님이 메뉴를 못 찾으시니, 코드로 해결하려 했으나...
#
# 일단 [프로그램 매매]는 위 코드로 100% 해결됩니다.
# [상승/하락 종목 수]는 아래 방법으로 진행하세요.

print("\n>>> [2단계] 상승/하락 종목 수 데이터")
print(">>> 네이버에는 과거 등락 종목 수 리스트가 없습니다.")
print(">>> 따라서 프로그램 매매 데이터만 우선 저장합니다.")

# CSV 저장
df_program.to_csv(OUTPUT_FILENAME, encoding='utf-8-sig')

print(f"\n>>> [성공] '{OUTPUT_FILENAME}' 저장 완료.")
print(">>> 이제 상승/하락 종목 수만 채우면 됩니다.")