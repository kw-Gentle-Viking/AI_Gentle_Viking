import sys
import os
import time
import pandas as pd
import win32com.client
import ctypes

# ========================================================
# [핵심] API 제한 체크 클래스 (과부하 방지용)
# ========================================================
class CpLimitManager:
    def __init__(self):
        self.objCpCybos = win32com.client.Dispatch("CpUtil.CpCybos")

    def wait_if_limit_reached(self):
        # 주문/계좌 관련이 아닌 시세 조회(LT_NONTRADE_REQUEST) 제한 확인
        remain_count = self.objCpCybos.GetLimitRemainCount(1) # 1: 시세 제한
        remain_time = self.objCpCybos.LimitRequestRemainTime / 1000.0 # 남은 시간(ms -> sec)

        # 안전하게 남은 횟수가 3회 미만이면 대기
        if remain_count <= 3:
            print(f"\n⏳ API 제한 대기... ({remain_time:.1f}초 대기)")
            time.sleep(remain_time + 0.5) # 여유 있게 0.5초 더 대기
            print("▶ 재개")

# ========================================================

def InitPlusCheck():
    if ctypes.windll.shell32.IsUserAnAdmin():
        print('✅ 정상: 관리자 권한으로 실행되었습니다.')
    else:
        print('❌ 오류: 관리자 권한이 없습니다. VS Code/IDLE을 [관리자 권한으로 실행] 하세요.')
        return False
    
    objCpUtil = win32com.client.Dispatch('CpUtil.CpCybos')
    if objCpUtil.IsConnect == 0:
        print("❌ 오류: 플러스가 연결되지 않았습니다.")
        return False
    return True

class DetailedFlowCollector:
    def __init__(self):
        self.objSvr7254 = win32com.client.Dispatch("CpSysDib.CpSvr7254")
        self.limit_manager = CpLimitManager() # 제한 관리자 추가

    def get_flow_data(self, full_code, start_date, end_date):
        all_data = []
        short_code = full_code[1:] if full_code.startswith('A') else full_code
        
        # [입력값 설정]
        self.objSvr7254.SetInputValue(0, full_code)
        self.objSvr7254.SetInputValue(1, 6)           # 6: 일자별 조회
        self.objSvr7254.SetInputValue(4, ord('0'))    # '0': 순매수
        self.objSvr7254.SetInputValue(5, 0)           # 0: 전체 투자자
        self.objSvr7254.SetInputValue(6, ord('2'))    # '2': 추정금액 (백만원)

        while True:
            # ★ 요청 전에 제한 확인 (가장 중요)
            self.limit_manager.wait_if_limit_reached()

            self.objSvr7254.BlockRequest()
            
            # 통신 상태 확인
            if self.objSvr7254.GetDibStatus() != 0:
                print(f"❌ API 에러: {self.objSvr7254.GetDibMsg1()}")
                # 에러가 나더라도 지금까지 수집한 거라도 반환하기 위해 break
                break

            count = self.objSvr7254.GetHeaderValue(1)
            if count == 0: break
            
            for i in range(count):
                raw_date = self.objSvr7254.GetDataValue(0, i)
                if raw_date > end_date: continue
                if raw_date < start_date: return all_data

                s_date = str(raw_date)
                db_date = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:]}"
                
                # 원 단위 변환 상수
                unit = 1000000 

                all_data.append({
                    'ticker': short_code,
                    'trade_date': db_date,
                    'fin_inv_net_amt': int(self.objSvr7254.GetDataValue(4, i) * unit),
                    'trust_net_amt': int(self.objSvr7254.GetDataValue(6, i) * unit),
                    'pension_net_amt': int(self.objSvr7254.GetDataValue(9, i) * unit),
                    'private_equity_net_amt': int(self.objSvr7254.GetDataValue(12, i) * unit),
                    'bank_net_amt': int(self.objSvr7254.GetDataValue(7, i) * unit),
                    'insurance_net_amt': int(self.objSvr7254.GetDataValue(5, i) * unit),
                    'etc_finance_net_amt': int(self.objSvr7254.GetDataValue(8, i) * unit),
                    'etc_corp_net_amt': int(self.objSvr7254.GetDataValue(10, i) * unit),
                    'etc_foreign_net_amt': int(self.objSvr7254.GetDataValue(11, i) * unit)
                })

            if not self.objSvr7254.Continue: break
            
            # 연속 조회 시에도 약간의 텀을 줌
            time.sleep(0.3) 

        return all_data

def run_collection(input_excel):
    if not InitPlusCheck(): return

    output_dir = "KOSPI_flow_detailed"
    if not os.path.exists(output_dir): os.makedirs(output_dir)

    try:
        df_input = pd.read_excel(input_excel)
        print(f"📂 엑셀 로드 완료: {len(df_input)}개 종목")
    except Exception as e:
        print(f"❌ 엑셀 읽기 실패: {e}")
        return

    collector = DetailedFlowCollector()
    TARGET_START = 20200102
    TARGET_END = 20251230

    success_count = 0
    # 이미 수집된 파일은 건너뛰기 (이어하기 기능)
    existing_files = os.listdir(output_dir)

    for idx, row in df_input.iterrows():
        raw_val = str(row['코드']).strip()
        clean_code = (raw_val[1:] if raw_val.startswith('A') else raw_val).split('.')[0].zfill(6)
        full_code = 'A' + clean_code
        
        # 저장될 파일명 예상
        expected_filename = f"KOSPI_A{clean_code}_flow_detailed.csv"
        
        # 이미 파일이 있다면 스킵 (시간 절약)
        if expected_filename in existing_files:
            print(f"⏭️ [{idx+1}/{len(df_input)}] {full_code} 이미 있음. 건너뜀.")
            success_count += 1
            continue

        print(f"🚀 [{idx+1}/{len(df_input)}] {full_code} 수집 중...", end="\r")
        
        try:
            flow_data = collector.get_flow_data(full_code, TARGET_START, TARGET_END)
            
            # 데이터가 1개라도 있으면 저장 (6년치가 아니어도 저장)
            if flow_data and len(flow_data) > 0:
                df_temp = pd.DataFrame(flow_data)
                df_temp = df_temp.sort_values(by='trade_date', ascending=True)
                
                file_path = os.path.join(output_dir, expected_filename)
                
                cols = ['ticker', 'trade_date', 'fin_inv_net_amt', 'trust_net_amt', 
                        'pension_net_amt', 'private_equity_net_amt', 'bank_net_amt', 
                        'insurance_net_amt', 'etc_finance_net_amt', 'etc_corp_net_amt', 
                        'etc_foreign_net_amt']
                df_temp[cols].to_csv(file_path, index=False, encoding='utf-8-sig')
                success_count += 1
            else:
                print(f"\n⚠️ {full_code} 데이터 없음 (수집 실패)")
            
            # 종목 간 대기 시간 (안전하게 0.5초)
            time.sleep(0.5)

        except Exception as e:
            print(f"\n❌ {full_code} 에러: {e}")

    print("\n" + "="*50)
    print(f"🏁 수집 종료! 성공: {success_count}건 / 저장: {os.path.abspath(output_dir)}")

if __name__ == "__main__":
    run_collection("KOSPI200_종목리스트.xlsx")