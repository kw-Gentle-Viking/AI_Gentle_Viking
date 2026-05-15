import OpenDartReader
import pandas as pd
import time
import os
import sys
import contextlib
from datetime import datetime

# ==========================================
# [설정] API 키 입력
# ==========================================
API_KEY = '0bf0bdae6a7f53b26d77e9acbf4b7924a2060240' 

@contextlib.contextmanager
def suppress_stdout():
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

def extract_from_full_report(df):
    """ 
    DART 재무제표 원본에서 계정과목(Account)을 식별하여 값 추출
    - 계산(뺄셈) 없음
    - 지배주주 순이익 식별 로직 유지
    - 총포괄손익 제외 로직 유지
    """
    if df is None or df.empty: return None

    data = {
        'revenue': 0, 'operating_profit': 0, 'net_profit': 0, 'equity': 0, 'debt': 0
    }

    candidates = {
        'revenue': [], 'operating': [], 
        'net_controlling': [], 'net_general': [], 
        'equity': [], 'debt': [], 'asset': [] 
    }

    for ix, row in df.iterrows():
        sj_nm = str(row.get('sj_nm', '')).replace(" ", "")
        is_IS = any(k in sj_nm for k in ['손익', 'IS', 'CIS']) 
        is_BS = any(k in sj_nm for k in ['상태', 'BS'])        
        acc_id = str(row.get('account_id', ''))
        raw_nm = str(row['account_nm'])
        
        if pd.isna(raw_nm): continue
        acct = raw_nm.replace(" ", "").replace("(", "").replace(")", "").replace(".", "").replace(",", "").strip()
        
        try: 
            amt_str = str(row['thstrm_amount'])
            if amt_str in ['-', '', 'nan', 'None']: amt = 0
            elif "(" in amt_str and ")" in amt_str:
                clean_str = amt_str.replace("(", "").replace(")", "").replace(",", "")
                amt = int(float(clean_str)) * -1
            else:
                amt = int(float(amt_str.replace(',', '')))
        except: amt = 0

        # [1] 매출액
        if is_IS:
            is_revenue = False
            if any(k in acct for k in ['매출', '영업수익', '이자수익', '보험료수익', '순영업수익', '수익']):
                is_revenue = True
            if is_revenue:
                if any(ban in acct for ban in ['원가', '채권', '총이익', '영업외', '기타', '미수', '이연', '현금', '비용', '부채']):
                    is_revenue = False
            if is_revenue: candidates['revenue'].append(amt)

        # [2] 영업이익
        if is_IS:
            if ('영업이익' in acct) or ('영업손실' in acct) or ('영업손익' in acct):
                if not any(ban in acct for ban in ['비용', '영업외', '현금', '활동']):
                    candidates['operating'].append(amt)

        # [3] 당기순이익 (지배주주 우선, 총포괄 제외)
        if is_IS:
            if 'ProfitLossAttributableToOwnersOfParent' in acc_id:
                candidates['net_controlling'].append(amt)
                continue 
            if 'ComprehensiveIncome' in acc_id: continue 

            if ('순이익' in acct or '순손실' in acct or '순손익' in acct):
                if '지배' in acct and '포괄' not in acct:
                    candidates['net_controlling'].append(amt)
                elif any(k in acct for k in ['당기', '분기', '반기', '연결']):
                    if not any(ban in acct for ban in ['포괄', '주당', '차감전', '현금', '평가']):
                        candidates['net_general'].append(amt)
            elif acct in ['지배기업소유주지분', '지배기업의소유주지분', '지배주주지분']:
                 candidates['net_controlling'].append(amt)

        # [4] 자본/부채/자산
        if is_BS:
            if acct in ['자본총계', '자본', '지배기업소유주지분', '지배지분', '자기자본', '기말자본']:
                candidates['equity'].append(amt)
            elif acct in ['부채총계', '부채', '기말부채', '총부채']:
                if '자본' not in acct: candidates['debt'].append(amt)
            elif acct in ['자산총계', '자산', '기말자산', '총자산']:
                candidates['asset'].append(amt)

    # 최종 값 결정
    if candidates['revenue']: data['revenue'] = max(candidates['revenue'])
    if candidates['operating']: data['operating_profit'] = sorted(candidates['operating'], key=abs, reverse=True)[0]
    
    if candidates['net_controlling']:
        data['net_profit'] = sorted(candidates['net_controlling'], key=abs, reverse=True)[0]
    elif candidates['net_general']:
        data['net_profit'] = sorted(candidates['net_general'], key=abs, reverse=True)[0]
    
    final_debt = sorted(candidates['debt'], key=abs, reverse=True)[0] if candidates['debt'] else 0
    final_asset = sorted(candidates['asset'], key=abs, reverse=True)[0] if candidates['asset'] else 0
    data['debt'] = final_debt

    if candidates['equity']: data['equity'] = sorted(candidates['equity'], key=abs, reverse=True)[0]
    if data['equity'] == 0 and final_asset != 0: data['equity'] = final_asset - final_debt

    if data['revenue'] == 0 and data['equity'] == 0 and data['operating_profit'] == 0: return None
    return data

def get_financial_summary(dart, corp_code, bsns_year, reprt_code):
    try:
        if int(bsns_year) > 2026: return None

        final_data = None
        pub_date = None 

        try:
            with suppress_stdout():
                # 연결재무제표(CFS) 우선
                df_cfs = dart.finstate_all(corp_code, bsns_year, reprt_code=reprt_code, fs_div='CFS')
        except: df_cfs = None
        
        if df_cfs is not None and not df_cfs.empty:
            if 'rcept_no' in df_cfs.columns:
                r_no = str(df_cfs['rcept_no'].iloc[0])
                if len(r_no) >= 8:
                    pub_date = f"{r_no[:4]}-{r_no[4:6]}-{r_no[6:8]}"
            
            final_data = extract_from_full_report(df_cfs)

        if final_data is None: return None

        final_data['pub_date'] = pub_date
        return final_data

    except:
        return None

def run_dart_collection(input_excel):
    output_dir = "KOSDAQ_finance_quarterly"
    if not os.path.exists(output_dir): 
        os.makedirs(output_dir)
        print(f"📂 폴더 생성됨: {output_dir}")

    with suppress_stdout():
        dart = OpenDartReader(API_KEY)
        
    try:
        df_input = pd.read_excel(input_excel)
        print(f"📂 엑셀 로드 완료: {len(df_input)}개 종목")
        cols = list(df_input.columns)
        code_col = next((c for c in cols if c in ['코드', '종목코드', 'Code', 'Symbol']), cols[0])
        name_col = next((c for c in cols if c in ['종목명', '기업명', 'Name', 'ItemName']), cols[1])
        print(f"✅ 매핑 완료: 코드='{code_col}', 종목명='{name_col}'")

    except Exception as e:
        print(f"❌ 엑셀 읽기 실패: {e}")
        return

    report_codes = [('11013', '1Q'), ('11012', '2Q'), ('11014', '3Q'), ('11011', '4Q')]
    date_map = {'1Q': '-03', '2Q': '-06', '3Q': '-09', '4Q': '-12'}
    years = range(2019, datetime.now().year + 1)

    for idx, row in df_input.iterrows():
        raw_code = str(row[code_col]).strip()
        clean_code = (raw_code[1:] if raw_code.startswith('A') else raw_code).split('.')[0].zfill(6)
        stock_name = str(row[name_col]).strip()
        
        print(f"🚀 [{idx+1}/{len(df_input)}] {stock_name}({clean_code}) 처리 중...", end="\r")

        try:
            with suppress_stdout(): corp_code = dart.find_corp_code(clean_code)
        except: corp_code = None
        
        if not corp_code:
            try:
                with suppress_stdout(): corp_code = dart.find_corp_code(stock_name)
            except: continue

        all_fin_data = []
        
        for year in years:
            for r_code, q_name in report_codes:
                if year == datetime.now().year and r_code == '11011': pass

                fin = get_financial_summary(dart, corp_code, str(year), r_code)
                
                if fin:
                    db_date = f"{year}{date_map[q_name]}"
                    
                    # 원본 데이터 그대로 저장 (변수명 단순화)
                    all_fin_data.append({
                        'ticker': clean_code,
                        'stock_name': stock_name,
                        'account_date': db_date,
                        'pub_date': fin.get('pub_date'),
                        'report_type': q_name,
                        'revenue': fin['revenue'],
                        'operating_profit': fin['operating_profit'],
                        'net_profit': fin['net_profit'],
                        'equity': fin['equity'],
                        'debt': fin['debt']
                    })
                    time.sleep(0.05) 

        if all_fin_data:
            # 경고 메시지 로직 (0 데이터 체크만 수행)
            warnings = []
            prev_equity = None
            
            for item in all_fin_data:
                date = item['account_date']
                
                # 값 0 체크
                check_targets = {
                    'revenue':'매출', 'operating_profit':'영업이익', 
                    'net_profit':'순이익', 'equity':'자본', 'debt':'부채'
                }
                for key, label in check_targets.items():
                    if item[key] == 0:
                        warnings.append(f"{date}({label}0)")
                
                # 자본 튀음 체크
                if prev_equity is not None and prev_equity > 1_000_000_000:
                    if item['equity'] != 0: 
                        ratio = item['equity'] / prev_equity
                        if ratio > 3.0 or ratio < 0.33:
                            warnings.append(f"{date}(자본튀음📉📈)")
                prev_equity = item['equity']

            if warnings:
                print(f"\n⚠️  [데이터 점검 필요] {stock_name}: {', '.join(warnings)}")

            # 파일 저장
            df_res = pd.DataFrame(all_fin_data)
            col_order = [
                'ticker', 'account_date', 'report_type',
                'revenue', 'operating_profit', 'net_profit', 'equity', 'debt', 'pub_date'
            ]
            final_cols = [c for c in col_order if c in df_res.columns]
            df_res = df_res[final_cols]
            
            filename = f"KOSDAQ_{clean_code}_finance_quarterly.csv"
            path = os.path.join(output_dir, filename)
            df_res.to_csv(path, index=False, encoding='utf-8-sig')
        
        else:
            print(f"\n❌ [수집 실패] {stock_name}: 연결재무제표 데이터 없음")
        
        time.sleep(0.1)

    print("\n🏁 수집 종료! (KOSDAQ_finance_quarterly 폴더 확인)")

if __name__ == "__main__":
    run_dart_collection("KOSDAQ150_종목리스트.xlsx")