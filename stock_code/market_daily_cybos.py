import sys
import os
import time
import pandas as pd
import win32com.client
import ctypes

def InitPlusCheck():
    # 관리자 권한 확인 (대신증권 API 필수 조건)
    if ctypes.windll.shell32.IsUserAnAdmin():
        print('✅ 정상: 관리자 권한으로 실행되었습니다.')
    else:
        print('❌ 오류: 관리자 권한으로 실행해 주세요. (대신증권 API는 관리자 권한이 필수입니다.)')
        return False
    
    # 연결 상태 확인
    objCpUtil = win32com.client.Dispatch('CpUtil.CpCybos')
    if objCpUtil.IsConnect == 0:
        print("❌ 오류: Cybos Plus가 연결되지 않았습니다. 로그인을 확인하세요.")
        return False
    return True

class MarketDataCollector:
    def __init__(self):
        # 1. 기존 지수 수집용
        self.objStockChart = win32com.client.Dispatch("CpSysDib.StockChart")
        # 2. [추가] 시장 전체 프로그램 매매 수집용 (Dscbo1.CpSvr8116)
        self.objProgram = win32com.client.Dispatch("Dscbo1.CpSvr8116")

    def get_market_indices(self, start_date, end_date):
        """시장 공통 지표 수집 (Raw 데이터 보존 버전)"""
        indices = {
            'U001': 'kospi_close',
            'U201': 'kosdaq_close',
            'U545': 'vkospi'
        }
        
        master_df = None
        
        for code, name in indices.items():
            try:
                print(f"📊 {name}({code}) Raw 데이터 요청 중...")
                
                self.objStockChart.SetInputValue(0, code)
                self.objStockChart.SetInputValue(1, ord('1'))    # 1: 기간으로 받기
                self.objStockChart.SetInputValue(2, end_date)   # 종료일
                self.objStockChart.SetInputValue(3, start_date) # 시작일
                self.objStockChart.SetInputValue(5, [0, 5])      # 0: 날짜, 5: 종가
                self.objStockChart.SetInputValue(6, ord('D'))    # 'D': 일봉
                self.objStockChart.SetInputValue(9, ord('1'))    # 1: 수정주가 반영
                
                self.objStockChart.BlockRequest()
                
                count = self.objStockChart.GetHeaderValue(3)
                if count == 0:
                    print(f"⚠️ {name}: 해당 기간에 데이터가 없습니다.")
                    continue

                temp_data = []
                for i in range(count):
                    raw_date = str(self.objStockChart.GetDataValue(0, i))
                    formatted_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    
                    temp_data.append({
                        'trade_date': formatted_date,
                        name: float(self.objStockChart.GetDataValue(1, i))
                    })
                
                df = pd.DataFrame(temp_data)
                
                if master_df is None:
                    master_df = df
                else:
                    master_df = pd.merge(master_df, df, on='trade_date', how='outer')
                
                time.sleep(0.25)
                
            except Exception as e:
                print(f"❌ {name} 수집 실패: {e}")
                continue
                
        return master_df

    def get_program_trend(self, start_date):
        """
        [NEW] Dscbo1.CpSvr8116 활용
        KOSPI 시장 전체 일별 프로그램 순매수 수집
        """
        print(f"🤖 KOSPI 프로그램 매매(8116) 수집 시작...")
        
        # 명세서 기준 입력 설정
        # 0 - (char) 장구분코드: '1' (거래소/KOSPI)
        self.objProgram.SetInputValue(0, ord('1'))
        
        data_list = []
        is_end = False
        
        while not is_end:
            self.objProgram.BlockRequest()
            
            # 통신 상태 확인
            if self.objProgram.GetDibStatus() != 0:
                print("❌ 통신 에러:", self.objProgram.GetDibMsg1())
                break

            # 명세서 기준 헤더: 1 - (short) 수신개수
            count = self.objProgram.GetHeaderValue(1)
            
            if count == 0:
                break
                
            for i in range(count):
                # 0 - (long) 일자
                raw_date = str(self.objProgram.GetDataValue(0, i))
                
                # 수집 목표일보다 과거면 중단
                if int(raw_date) < int(start_date):
                    is_end = True
                    break
                
                fmt_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                
                # 7 - (long) 전체순매수금액 (문서 기준 Index 7)
                # 단위: 보통 백만원 단위이므로 원 단위(BigInt)로 변환 (* 1,000,000)
                net_buy_val = self.objProgram.GetDataValue(7, i)
                net_buy_won = int(net_buy_val) * 1_000_000
                
                data_list.append({
                    'trade_date': fmt_date,
                    'program_net_amt': net_buy_won
                })
            
            if is_end: break
            
            # 연속 데이터 확인 (CpSvr8116은 연속여부 O)
            if self.objProgram.Continue == False:
                print("🏁 데이터 마지막 페이지 도달")
                break
            
            # 진행상황 출력 (너무 자주 출력되지 않게 50개 단위로 끊거나 그냥 둠)
            # print(f"   - {len(data_list)}일치 수집 중...")
            time.sleep(0.25)

        print(f"   - 프로그램 데이터 총 {len(data_list)}건 확보.")
        return pd.DataFrame(data_list)

def run_market_collection():
    if not InitPlusCheck(): return

    output_dir = "Raw_Market_Data"
    if not os.path.exists(output_dir): os.makedirs(output_dir)

    collector = MarketDataCollector()
    TARGET_START, TARGET_END = 20200102, 20251230

    print(f"🚀 {TARGET_START} ~ {TARGET_END} 데이터 통합 수집 시작...")
    
    # 1. 지수 데이터 수집
    market_df = collector.get_market_indices(TARGET_START, TARGET_END)
    
    # 2. 프로그램 매매 데이터 수집 (CpSvr8116)
    program_df = collector.get_program_trend(TARGET_START)

    # 3. 데이터 병합
    if market_df is not None and not program_df.empty:
        print("🔗 지수 데이터와 프로그램 데이터를 병합합니다...")
        
        # trade_date 기준으로 병합 (Outer Join)
        final_df = pd.merge(market_df, program_df, on='trade_date', how='outer')
        
        # 4. 시간순 정렬 및 필터링
        final_df = final_df.sort_values(by='trade_date', ascending=True)
        
        # 날짜 포맷 문자열로 변환하여 비교
        s_date_str = f"{str(TARGET_START)[:4]}-{str(TARGET_START)[4:6]}-{str(TARGET_START)[6:]}"
        e_date_str = f"{str(TARGET_END)[:4]}-{str(TARGET_END)[4:6]}-{str(TARGET_END)[6:]}"
        final_df = final_df[(final_df['trade_date'] >= s_date_str) & (final_df['trade_date'] <= e_date_str)]

        # 5. 저장
        save_path = os.path.join(output_dir, "market_daily_cybos_final.csv")
        final_df.to_csv(save_path, index=False, encoding='utf-8-sig')
        
        print("\n" + "="*50)
        print(f"✅ 전체 Raw 데이터 수집 및 병합 완료!")
        print(f"📂 저장 경로: {save_path}")
        print(f"📊 총 라인수: {len(final_df)}행")
        print("="*50)
        print(final_df.tail(10))
    else:
        print("❌ 수집 실패: 주요 데이터프레임이 비어있습니다.")

if __name__ == "__main__":
    run_market_collection()