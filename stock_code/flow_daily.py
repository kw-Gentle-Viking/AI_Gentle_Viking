import sys
import os
import time
import pandas as pd
import win32com.client
import ctypes

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

class HistoricalFlowCollector:
    def __init__(self):
        self.objStockChart = win32com.client.Dispatch("CpSysDib.StockChart")

    def get_flow_data(self, full_code, start_date, end_date):
        all_data = []
        short_code = full_code[1:] if full_code.startswith('A') else full_code
        
        request_start = 20191215 

        self.objStockChart.SetInputValue(0, full_code)
        self.objStockChart.SetInputValue(1, ord('1'))
        self.objStockChart.SetInputValue(2, end_date)
        self.objStockChart.SetInputValue(3, request_start)
        self.objStockChart.SetInputValue(5, [0, 5, 13, 16, 21]) 
        self.objStockChart.SetInputValue(6, ord('D'))
        self.objStockChart.SetInputValue(9, ord('1'))

        self.objStockChart.BlockRequest()
        count = self.objStockChart.GetHeaderValue(3)
    
        if count <= 1: return None

        raw_list = []
        for i in range(count):
            raw_list.append({
                'date': self.objStockChart.GetDataValue(0, i),
                'close': self.objStockChart.GetDataValue(1, i),
                'mkt_cap': self.objStockChart.GetDataValue(2, i),
                'frgn_acc': self.objStockChart.GetDataValue(3, i),
                'inst_acc': self.objStockChart.GetDataValue(4, i)
            })

        raw_list.sort(key=lambda x: x['date'])

        for i in range(1, len(raw_list)):
            curr = raw_list[i]
            prev = raw_list[i-1]
            if curr['date'] < start_date: continue

            # 1. 일일 순매수 수량 계산
            dq_frgn = curr['frgn_acc'] - prev['frgn_acc']
            dq_inst = curr['inst_acc'] - prev['inst_acc']
            dq_indiv = -(dq_frgn + dq_inst) 

            # --- [수정] 날짜 형식을 DB 형식(YYYY-MM-DD)으로 변환 ---
            s_date = str(curr['date'])
            db_date = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:]}"
            # --------------------------------------------------

            all_data.append({
                'ticker': short_code,
                'trade_date': db_date, # 변환된 날짜 적용
                'individual_net_amt': int(dq_indiv * curr['close']),
                'foreign_net_amt': int(dq_frgn * curr['close']),
                'inst_net_amt': int(dq_inst * curr['close']),
                'market_cap': curr['mkt_cap']
            })
        return all_data

def run_collection(input_excel):
    if not InitPlusCheck(): return

    output_dir = "KOSDAQ_flow_daily"
    if not os.path.exists(output_dir): os.makedirs(output_dir)

    try:
        df_input = pd.read_excel(input_excel)
        print(f"📂 엑셀 로드 완료: {len(df_input)}개 종목")
    except Exception as e:
        print(f"❌ 엑셀 읽기 실패: {e}")
        return

    collector = HistoricalFlowCollector()
    TARGET_START = 20200102
    TARGET_END = 20251230

    success_count = 0
    for idx, row in df_input.iterrows():
        raw_val = str(row['코드']).strip()
        clean_code = raw_val[1:] if raw_val.startswith('A') else raw_val
        if '.' in clean_code: clean_code = clean_code.split('.')[0]
        
        short_code = clean_code.zfill(6)
        full_code = 'A' + short_code

        print(f"🚀 [{idx+1}/{len(df_input)}] {full_code} 수집 중...", end="\r")
        
        try:
            flow_data = collector.get_flow_data(full_code, TARGET_START, TARGET_END)
            
            if flow_data:
                df_temp = pd.DataFrame(flow_data)
                file_path = os.path.join(output_dir, f"KOSDAQ_A{short_code}_flow_daily.csv")
                df_temp.to_csv(file_path, index=False, encoding='utf-8-sig', float_format='%.0f')
                success_count += 1
            
            time.sleep(0.25)

        except Exception as e:
            print(f"\n❌ {full_code} 에러: {e}")

    print("\n" + "="*50)
    print(f"🏁 수집 종료! 성공: {success_count}건 / 저장: {os.path.abspath(output_dir)}")

if __name__ == "__main__":
    run_collection("KOSDAQ150_종목리스트.xlsx")