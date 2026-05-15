import sys
import time
import os
import pandas as pd
import win32com.client
from PyQt5.QtWidgets import *

# 대신증권 공통 객체
g_objCodeMgr = win32com.client.Dispatch('CpUtil.CpCodeMgr')
g_objCpStatus = win32com.client.Dispatch('CpUtil.CpCybos')

class CpStockChart:
    def __init__(self):
        self.objStockChart = win32com.client.Dispatch("CpSysDib.StockChart")

    def RequestMinuteData(self, code, targetDate, caller):
        # 데이터 초기화 (호가 잔량 리스트 추가)
        caller.dates, caller.times, caller.opens, caller.highs = [], [], [], []
        caller.lows, caller.closes, caller.vols, caller.turnovers = [], [], [], []
        caller.bid_sizes, caller.ask_sizes = [], [] # 매수/매도 잔량 리스트

        print(f"[{code}] 5분봉 조회 시작: 20251230부터 {targetDate}까지")

        self.objStockChart.SetInputValue(0, code)
        self.objStockChart.SetInputValue(1, ord('1')) # 기간 기준
        self.objStockChart.SetInputValue(2, 20251230) # 수집 종료일
        self.objStockChart.SetInputValue(3, targetDate) # 수집 시작일
        
        # 0:일자, 1:시간, 2:시가, 3:고가, 4:저가, 5:종가, 8:거래량, 9:거래대금, 10:누적매도수량, 11:누적매수수량
        self.objStockChart.SetInputValue(5, [0, 1, 2, 3, 4, 5, 8, 9, 10, 11]) 
        self.objStockChart.SetInputValue(6, ord('m')) # 분봉
        self.objStockChart.SetInputValue(7, 5)        # 5분봉
        self.objStockChart.SetInputValue(9, ord('1')) # 수정주가

        while True:
            self.objStockChart.BlockRequest()

            if self.objStockChart.GetDibStatus() != 0:
                print("통신 에러:", self.objStockChart.GetDibMsg1())
                break

            count = self.objStockChart.GetHeaderValue(3)
            if count == 0:
                break

            last_date_in_block = 0
            for i in range(count):
                date = self.objStockChart.GetDataValue(0, i)
                last_date_in_block = date
                
                if date < targetDate:
                    break
                
                caller.dates.append(date)
                caller.times.append(self.objStockChart.GetDataValue(1, i))
                caller.opens.append(self.objStockChart.GetDataValue(2, i))
                caller.highs.append(self.objStockChart.GetDataValue(3, i))
                caller.lows.append(self.objStockChart.GetDataValue(4, i))
                caller.closes.append(self.objStockChart.GetDataValue(5, i))
                caller.vols.append(self.objStockChart.GetDataValue(6, i))
                caller.turnovers.append(self.objStockChart.GetDataValue(7, i))
                caller.ask_sizes.append(self.objStockChart.GetDataValue(8, i)) # 필드 10
                caller.bid_sizes.append(self.objStockChart.GetDataValue(9, i)) # 필드 11

            if not self.objStockChart.Continue or last_date_in_block <= targetDate:
                break
            
            time.sleep(0.3) 

        print(f"[{code}] 수집 완료! (총 {len(caller.dates)}행)")
        return True

class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 잔량 리스트 추가
        self.dates, self.opens, self.highs, self.lows, self.closes, self.vols, self.times, self.turnovers, self.bid_sizes, self.ask_sizes = [], [], [], [], [], [], [], [], [], []
        self.objChart = CpStockChart()

        self.setWindowTitle("전종목 5분봉 수집기 (잔량 포함)")
        self.resize(400, 200)

        layout = QVBoxLayout()
        self.btn_auto = QPushButton("2025-12-30 기준 수집 시작")
        self.btn_auto.setMinimumHeight(60)
        self.btn_auto.clicked.connect(self.start_batch_process)
        layout.addWidget(self.btn_auto)

        self.status_label = QLabel("준비 완료")
        layout.addWidget(self.status_label)

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

    def start_batch_process(self):
        excel_file = 'KOSPI200_종목리스트.xlsx'
        if not os.path.exists(excel_file):
            print(f"파일을 찾을 수 없습니다: {excel_file}")
            return

        try:
            df_list = pd.read_excel(excel_file)
            codes = df_list['코드'].tolist()
        except Exception as e:
            print(f"엑셀 읽기 오류: {e}")
            return

        for code in codes:
            str_code = str(code).strip()
            if not str_code.startswith('A'):
                str_code = 'A' + str_code.zfill(6)

            name = g_objCodeMgr.CodeToName(str_code)
            self.status_label.setText(f"현재 수집 중: {name}")
            QApplication.processEvents()

            # 목표 날짜 설정
            self.objChart.RequestMinuteData(str_code, 20240102, self)

            if self.dates:
                self.save_to_csv(code)
            
            time.sleep(1.0)

        self.status_label.setText("지정 기간 수집 완료!")

    def save_to_csv(self, code):
        str_code = str(code).strip()
        clean_code = str_code[1:] if str_code.startswith('A') else str_code.zfill(6)
            
        filename = f"KOSPI_A{clean_code}_5min_data.csv"
        
        # 1. 초기 DataFrame 생성
        df = pd.DataFrame({
            'ticker': clean_code,
            'trade_date': [pd.to_datetime(d, format='%Y%m%d').strftime('%Y-%m-%d') for d in self.dates],
            'trade_time': [f"{str(t).zfill(4)[:2]}:{str(t).zfill(4)[2:]}:00" for t in self.times],
            'open_price': self.opens, 
            'high_price': self.highs, 
            'low_price': self.lows, 
            'close_price': self.closes, 
            'volume': self.vols,
            'turnover': self.turnovers,
            'bid_size_total': self.bid_sizes,
            'ask_size_total': self.ask_sizes
        })

        # 2. 시간순 정렬
        df = df.sort_values(by=['trade_date', 'trade_time'], ascending=True)

        # 3. 구간 데이터 변환 (diff) 및 정수형(int) 강제 변환
        # fillna(0) 등을 통해 결측치를 확실히 처리한 후 astype(int)를 적용해야 합니다.
        df['bid_size_total'] = df.groupby('trade_date')['bid_size_total'].diff().fillna(df['bid_size_total']).astype(int)
        df['ask_size_total'] = df.groupby('trade_date')['ask_size_total'].diff().fillna(df['ask_size_total']).astype(int)
        
        # 4. 최종 컬럼 순서 조정
        final_df = df[['ticker', 'trade_date', 'trade_time', 'open_price', 'high_price', 
                       'low_price', 'close_price', 'volume', 'turnover', 
                       'bid_size_total', 'ask_size_total']]

        # 5. 저장 (float_format은 이제 필요 없지만 정수 보존을 위해 제거)
        final_df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"저장 성공: {filename}")
        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MyWindow()
    win.show()
    sys.exit(app.exec_())