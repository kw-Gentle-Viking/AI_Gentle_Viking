import win32com.client
import pandas as pd
import ctypes
import time

def InitPlusCheck():
    if ctypes.windll.shell32.IsUserAnAdmin():
        print('✅ 정상: 관리자 권한 실행 중')
    else:
        print('❌ 오류: 관리자 권한 필요')
        return False
    objCpUtil = win32com.client.Dispatch('CpUtil.CpCybos')
    if objCpUtil.IsConnect == 0:
        print("❌ 오류: 플러스 연결 실패")
        return False
    return True

def get_ticker_info_optimized(excel_path):
    if not InitPlusCheck(): return
    
    print("🚀 [최적화 버전] 마스터 정보 수집 시작 (상장주식수/종목코드 포맷 최적화)...")
    objCodeMgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")

    try:
        # 엑셀 로드 시 문자열로 읽고 6자리 0 채우기 (이미 숫자만 있는 상태)
        input_df = pd.read_excel(excel_path, dtype=str)
        # '코드' 컬럼에서 'A'를 제거하고 숫자 6자리만 추출하여 리스트화
        tickers = input_df['코드'].str.strip().str.replace('A', '', regex=False).str.zfill(6).tolist()
    except Exception as e:
        print(f"❌ 엑셀 로드 오류: {e}")
        return

    master_list = []
    
    for t in tickers:
        # --- [수정] 대신증권 API 요청 시에만 'A'를 붙임 ---
        full_code = f"A{t}" 
        name = objCodeMgr.CodeToName(full_code)
        
        if not name:
            continue

        # --- 1. 업종 정보 수집 (Sector 하나로 통합) ---
        sector_name = "N/A"
        try:
            ind_code = objCodeMgr.GetStockIndustryCode(full_code) 
            sector_name = objCodeMgr.GetIndustryName(ind_code) 
        except:
            pass

        # --- 2. 상장일 수집 (GetStockListedDate) ---
        listing_date = None
        try:
            l_date = objCodeMgr.GetStockListedDate(full_code) 
            if l_date > 0:
                listing_date = pd.to_datetime(str(l_date), format='%Y%m%d').strftime('%Y-%m-%d')
        except:
            pass

        # --- 3. 소속부 수집 (GetStockMarketKind) ---
        m_kind = objCodeMgr.GetStockMarketKind(full_code) 
        market_map = {1: 'KOSPI', 2: 'KOSDAQ', 3: 'K-OTC', 4: 'KRX', 5: 'KONEX'}
        market_type = market_map.get(m_kind, 'ETC')

        # --- [수정] 결과 리스트에는 숫자 6자리(t)만 담음 ---
        master_list.append({
            "ticker": t,           # 'A'가 포함되지 않은 숫자 6자리 문자열
            "ticker_name": name,
            "market_type": market_type,
            "sector": sector_name, 
            "listing_date": listing_date
        })
        print(f"✅ {name}({t}) | {sector_name} | {listing_date}")
        time.sleep(0.01)

    # 최종 결과 저장
    result_df = pd.DataFrame(master_list)
    
    # [중요] 저장 시 ticker 컬럼이 숫자로 바뀌어 앞의 0이 사라지지 않도록 주의
    result_df.to_csv("raw_KOSPI_ticker_info.csv", index=False, encoding='utf-8-sig', quoting=1) # quoting=1은 모든 필드 따옴표 처리
    print(f"\n✨ 수집 종료! {len(result_df)}건의 데이터가 'raw_KOSPI_ticker_info.csv'에 저장되었습니다.")

if __name__ == "__main__":
    get_ticker_info_optimized("KOSPI200_종목리스트.xlsx")