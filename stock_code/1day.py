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

    def RequestFromTo(self, code, fromDate, toDate, caller):
        """지정한 시작일(fromDate)부터 종료일(toDate)까지의 일봉 데이터를 요청"""
        if g_objCpStatus.IsConnect == 0:
            print("PLUS가 정상적으로 연결되지 않음.")
            return False

        print(f"[{code}] {fromDate} ~ {toDate} 데이터 요청 중...")

        self.objStockChart.SetInputValue(0, code)      # 종목코드
        self.objStockChart.SetInputValue(1, ord('1'))  # 1: 기간으로 받기
        self.objStockChart.SetInputValue(2, toDate)    # 종료일
        self.objStockChart.SetInputValue(3, fromDate)  # 시작일
        
        # 필드 추가: 12번 상장주식수 추가 (SQL shares_outstanding 대응)
        # 0:날짜, 2:시가, 3:고가, 4:저가, 5:종가, 8:거래량, 9:거래대금, 12:상장주식수
        self.objStockChart.SetInputValue(5, [0, 2, 3, 4, 5, 8, 9, 12])  
        self.objStockChart.SetInputValue(6, ord('D'))  # 'D': 일봉
        self.objStockChart.SetInputValue(9, ord('1'))  # 수정주가 사용
        
        self.objStockChart.BlockRequest()

        if self.objStockChart.GetDibStatus() != 0:
            print("통신 에러:", self.objStockChart.GetDibMsg1())
            return False

        cnt = self.objStockChart.GetHeaderValue(3) 
        
        caller.dates = []
        caller.opens = []
        caller.highs = []
        caller.lows = []
        caller.closes = []
        caller.vols = []
        caller.turnovers = []
        caller.shares = [] # 상장주식수 리스트 추가

        for i in range(cnt):
            caller.dates.append(self.objStockChart.GetDataValue(0, i))
            caller.opens.append(self.objStockChart.GetDataValue(1, i))
            caller.highs.append(self.objStockChart.GetDataValue(2, i))
            caller.lows.append(self.objStockChart.GetDataValue(3, i))
            caller.closes.append(self.objStockChart.GetDataValue(4, i))
            caller.vols.append(self.objStockChart.GetDataValue(5, i))
            caller.turnovers.append(self.objStockChart.GetDataValue(6, i))
            caller.shares.append(self.objStockChart.GetDataValue(7, i)) # 7번째 인덱스에 저장됨
        
        return True

class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # self.shares 추가
        self.dates, self.opens, self.highs, self.lows, self.closes, self.vols, self.turnovers, self.shares = [], [], [], [], [], [], [], []
        self.objChart = CpStockChart()

        self.setWindowTitle("일봉 수집기 (DB 대응)")
        self.resize(400, 200)

        layout = QVBoxLayout()
        self.btn_start = QPushButton("엑셀 리스트 기간 데이터 수집 시작")
        self.btn_start.setMinimumHeight(80)
        self.btn_start.clicked.connect(self.start_collection)
        layout.addWidget(self.btn_start)

        self.status_label = QLabel("상태: 대기 중")
        layout.addWidget(self.status_label)

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

    def start_collection(self):
        excel_file = 'KOSDAQ150_종목리스트.xlsx'
        if not os.path.exists(excel_file):
            self.status_label.setText("에러: 엑셀 파일을 찾을 수 없습니다.")
            return

        try:
            df_list = pd.read_excel(excel_file)
            codes = df_list['코드'].tolist()
        except Exception as e:
            self.status_label.setText(f"에러: {e}")
            return

        for code in codes:
            str_code = str(code).strip()
            if not str_code.startswith('A'):
                str_code = 'A' + str_code.zfill(6)

            name = g_objCodeMgr.CodeToName(str_code)
            self.status_label.setText(f"진행 중: {name} ({str_code})")
            QApplication.processEvents()

            if self.objChart.RequestFromTo(str_code, 20200102, 20251230, self):
                if len(self.dates) > 0:
                    self.save_to_csv(str_code)
            
            time.sleep(0.3)

        self.status_label.setText("수집 완료!")

    def save_to_csv(self, str_code):
        # 종목코드 전처리
        clean_code = str_code[1:] if str_code.startswith('A') else str_code.zfill(6)
        filename = f"KOSDAQ_A{clean_code}_1day_data.csv"
        
        # SQL 테이블(raw.price_daily) 컬럼명에 맞춰 DataFrame 생성
        df = pd.DataFrame({
            'ticker': clean_code,
            'trade_date': [pd.to_datetime(d, format='%Y%m%d').strftime('%Y-%m-%d') for d in self.dates],
            'open_price': self.opens, 
            'high_price': self.highs, 
            'low_price': self.lows, 
            'close_price': self.closes, 
            'volume': self.vols,
            'turnover': self.turnovers,
            'shares_outstanding': self.shares
        })
        
        # 일자순 정렬
        df = df.sort_values(by='trade_date', ascending=True)
        
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"저장 성공: {filename}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MyWindow()
    win.show()
    sys.exit(app.exec_())