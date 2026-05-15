import sys
import os
import time
import pandas as pd
import win32com.client
import ctypes

# ==============================================================================
# 1. 초기화 및 권한 확인
# ==============================================================================
def InitPlusCheck():
    if ctypes.windll.shell32.IsUserAnAdmin():
        print('✅ 관리자 권한 확인 완료')
    else:
        print('❌ 오류: 관리자 권한이 없습니다. [관리자 권한으로 실행] 해주세요.')
        return False
    
    objCpUtil = win32com.client.Dispatch('CpUtil.CpCybos')
    if objCpUtil.IsConnect == 0:
        print("❌ 오류: 플러스가 연결되지 않았습니다.")
        return False
    return True

# ==============================================================================
# 2. 수집 클래스 (공매도 + 대차)
# ==============================================================================
class ShortLendingCollector:
    def __init__(self):
        # 1. 공매도 추이 (CpSvr7238)
        self.objShort = win32com.client.Dispatch("CpSysDib.CpSvr7238")
        # 2. 대차거래 (CpSvr7240)
        self.objLending = win32com.client.Dispatch("CpSysDib.CpSvr7240")
        # 3. 통신 제한 확인
        self.objCpCybos = win32com.client.Dispatch("CpUtil.CpCybos")

    def _check_limit(self):
        """통신 제한 확인 및 대기 함수 (등급 오류 방지용)"""
        remain = self.objCpCybos.GetLimitRemainCount(1) # 시세 제한 확인
        if remain <= 3:
            print(f"   ⏳ 요청 제한 근접 (남은 횟수: {remain}). 3초 대기...")
            time.sleep(3)

    # --- [공매도 추이 수집] ---
    def _get_short_raw(self, code, target_start):
        self.objShort.SetInputValue(0, code)
        data_list = []
        
        while True:
            self._check_limit() # 제한 확인
            
            self.objShort.BlockRequest()
            
            # 통신 상태 확인
            if self.objShort.GetDibStatus() != 0:
                print(f"⚠️ 통신상태 불량: {self.objShort.GetDibMsg1()}")
                break
            
            count = self.objShort.GetHeaderValue(0)
            if count == 0: break
            
            for i in range(count):
                dt = str(self.objShort.GetDataValue(0, i))
                
                # 목표 날짜보다 과거면 데이터 수집 중단 (더 볼 필요 없음)
                if int(dt) < target_start: 
                    return pd.DataFrame(data_list)
                
                fmt_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
                # 5:공매도량, 7:공매도대금
                data_list.append({
                    'trade_date': fmt_date,
                    'short_vol': self.objShort.GetDataValue(5, i),
                    'short_amt': self.objShort.GetDataValue(7, i)
                })
            
            if not self.objShort.Continue: break
            
            # [수정] 등급 오류 방지를 위해 대기 시간 증가 (0.2 -> 0.5)
            time.sleep(0.5)
            
        return pd.DataFrame(data_list)

    # --- [대차거래 수집] ---
    def _get_lending_raw(self, code, target_start):
        self.objLending.SetInputValue(0, code)
        data_list = []
        
        while True:
            self._check_limit() # 제한 확인
            
            self.objLending.BlockRequest()
            
            if self.objLending.GetDibStatus() != 0: break
            
            count = self.objLending.GetHeaderValue(0)
            if count == 0: break
            
            for i in range(count):
                dt = str(self.objLending.GetDataValue(0, i))
                
                if int(dt) < target_start: 
                    return pd.DataFrame(data_list)
                
                fmt_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
                # 10: 대차잔고금액
                data_list.append({
                    'trade_date': fmt_date,
                    'lending_balance_amt': self.objLending.GetDataValue(10, i)
                })
            
            if not self.objLending.Continue: break
            
            # [수정] 대기 시간 증가 (0.2 -> 0.5)
            time.sleep(0.5)
            
        return pd.DataFrame(data_list)

    # --- [통합 데이터 반환] ---
    def get_integrated_data(self, full_code, target_start_int):
        # 1. 수집 (Start Date까지만 가져옴)
        df_short = self._get_short_raw(full_code, target_start_int)
        df_lending = self._get_lending_raw(full_code, target_start_int)
        
        if df_short.empty and df_lending.empty:
            return None

        # 2. 병합 (Full Outer Join)
        if not df_short.empty and not df_lending.empty:
            merged = pd.merge(df_short, df_lending, on='trade_date', how='outer')
        elif not df_short.empty:
            merged = df_short
        else:
            merged = df_lending

        # 3. 결측치 처리
        merged = merged.fillna(0)
        
        short_code = full_code[1:] # A 제거
        merged['ticker'] = short_code
        
        # 4. 컬럼 정리 (SQL 스키마 순서)
        cols = ['ticker', 'trade_date', 'short_vol', 'short_amt', 'lending_balance_amt']
        for c in cols:
            if c not in merged.columns: merged[c] = 0
            
        return merged[cols]

# ==============================================================================
# 3. 실행 로직 (엑셀 읽기 -> CSV 저장)
# ==============================================================================
def run_collection(input_excel):
    if not InitPlusCheck(): return

    output_dir = "KOSPI_short_lending"
    if not os.path.exists(output_dir): os.makedirs(output_dir)

    try:
        df_input = pd.read_excel(input_excel)
        print(f"📂 엑셀 로드 완료: {len(df_input)}개 종목")
    except Exception as e:
        print(f"❌ 엑셀 읽기 실패: {e}")
        return

    collector = ShortLendingCollector()
    
    TARGET_START_INT = 20200102 
    START_DATE_STR = "2020-01-02"
    END_DATE_STR = "2025-12-30"

    success_count = 0
    
    for idx, row in df_input.iterrows():
        raw_val = str(row['코드']).strip()
        clean_code = raw_val[1:] if raw_val.startswith('A') else raw_val
        if '.' in clean_code: clean_code = clean_code.split('.')[0]
        
        short_code = clean_code.zfill(6)
        full_code = 'A' + short_code

        # ---------------------------------------------------------
        # [추가] 이어받기 로직: 이미 파일이 존재하면 건너뜁니다.
        # ---------------------------------------------------------
        file_path = os.path.join(output_dir, f"KOSPI_{full_code}_short_lending.csv")
        if os.path.exists(file_path):
            print(f"⏩ [{idx+1}/{len(df_input)}] {full_code} 이미 존재함. 건너뜁니다.")
            success_count += 1
            continue
        # ---------------------------------------------------------

        print(f"🚀 [{idx+1}/{len(df_input)}] {full_code} 수집 중...", end="\r")
        
        try:
            df_result = collector.get_integrated_data(full_code, TARGET_START_INT)
            
            if df_result is not None and not df_result.empty:
                df_result = df_result[
                    (df_result['trade_date'] >= START_DATE_STR) & 
                    (df_result['trade_date'] <= END_DATE_STR)
                ]

                if not df_result.empty:
                    df_result = df_result.sort_values(by='trade_date', ascending=True)
                    df_result['short_amt'] = df_result['short_amt'] * 10000
                    df_result['lending_balance_amt'] = df_result['lending_balance_amt'] * 1000000
                    
                    int_cols = ['short_vol', 'short_amt', 'lending_balance_amt']
                    df_result[int_cols] = df_result[int_cols].astype('int64')
                    
                    # 파일 저장
                    df_result.to_csv(file_path, index=False, encoding='utf-8-sig')
                    success_count += 1
            
            time.sleep(1.5)

        except Exception as e:
            print(f"\n❌ {full_code} 에러: {e}")

    print("\n" + "="*50)
    print(f"🏁 수집 종료! 성공: {success_count}건")
    print(f"📂 저장 경로: {os.path.abspath(output_dir)}")

if __name__ == "__main__":
    run_collection("KOSPI200_종목리스트.xlsx")