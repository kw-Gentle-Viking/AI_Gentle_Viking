import OpenDartReader
import pandas as pd
import time
import os
import sys
import contextlib

# ==========================================
# [설정]
# ==========================================
API_KEY = '0bf0bdae6a7f53b26d77e9acbf4b7924a2060240' 
INPUT_EXCEL = "KOSPI200_종목리스트.xlsx" 
OUTPUT_DIR = "KOSPI_stock_events" 

START_DATE = '20200101'
END_DATE = '20251231'

EVENT_KEYWORDS = {
    '유상증자': '유상증자',
    '무상증자': '무상증자',
    '배당': '배당락_배당결정',
    '주식분할': '액면분할',
    '실적': '실적발표',
    '잠정': '실적발표',
    '매출액또는손익': '실적발표',
    '합병': '합병'
}

@contextlib.contextmanager
def suppress_stdout():
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

def run_individual_collection():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

    dart = OpenDartReader(API_KEY)

    try:
        df_input = pd.read_excel(INPUT_EXCEL)
        print(f"📂 엑셀 로드 완료: {len(df_input)}개 종목")
    except Exception as e:
        print(f"❌ 엑셀 읽기 실패: {e}")
        return

    cols = df_input.columns
    name_col = next((c for c in cols if c in ['종목명', '종목 이름', '기업명', 'Name']), None)
    code_col = next((c for c in cols if c in ['코드', '종목코드', 'Symbol']), None)

    if not name_col or not code_col:
        print("❌ 엑셀 컬럼 매칭 실패.")
        return

    print(f"🚀 종목별 공시 이벤트 수집 시작 (날짜순 정렬됨)...\n")

    for idx, row in df_input.iterrows():
        raw_code = str(row[code_col]).strip()
        clean_code = (raw_code[1:] if raw_code.startswith('A') else raw_code).split('.')[0].zfill(6)
        stock_name = str(row[name_col])

        print(f"[{idx+1}/{len(df_input)}] {stock_name} ({clean_code}) 처리 중...", end="\r")

        current_stock_events = []

        try:
            with suppress_stdout():
                corp_code = dart.find_corp_code(clean_code)
            
            if corp_code:
                with suppress_stdout():
                    reports = dart.list(corp_code, start=START_DATE, end=END_DATE, final=False)

                if reports is not None and not reports.empty:
                    for r_idx, r_row in reports.iterrows():
                        report_nm = str(r_row['report_nm']).replace(" ", "")
                        
                        for key, val in EVENT_KEYWORDS.items():
                            if key in report_nm:
                                current_stock_events.append({
                                    'ticker': clean_code,
                                    'event_date': r_row['rcept_dt'],
                                    'event_type': val,
                                    'description': r_row['report_nm']
                                })
                                break
                
                if current_stock_events:
                    df_res = pd.DataFrame(current_stock_events)
                    
                    # [수정 1] 날짜 포맷 변환 (String -> Datetime)
                    df_res['event_date'] = pd.to_datetime(df_res['event_date'])
                    
                    # [수정 2] 날짜 기준 오름차순 정렬 (과거 -> 최신)
                    df_res = df_res.sort_values(by='event_date', ascending=True)
                    
                    # [수정 3] 다시 문자열로 변환 (YYYY-MM-DD)
                    df_res['event_date'] = df_res['event_date'].dt.strftime('%Y-%m-%d')
                    
                    cols = ['ticker', 'event_date', 'event_type', 'description']
                    df_res = df_res[cols]
                    
                    save_path = os.path.join(OUTPUT_DIR, f"KOSPI_{clean_code}_stock_events.csv")
                    df_res.to_csv(save_path, index=False, encoding='utf-8-sig')

                time.sleep(0.1) 
        
        except Exception as e:
            continue

    print("\n\n🏁 수집 완료! (모든 파일이 날짜순으로 정렬되어 저장되었습니다)")

if __name__ == "__main__":
    run_individual_collection()