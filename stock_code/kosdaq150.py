import win32com.client
import pandas as pd
import os

# 1. 대신증권 API 객체 생성
objCodeMgr = win32com.client.Dispatch('CpUtil.CpCodeMgr')

def save_kosdaq150_common_stocks():
    # 2. 코스닥 150 구성 종목 코드 리스트 가져오기 (그룹번호 390)
    kosdaq150_codes = objCodeMgr.GetGroupCodeList(390)
    
    codes = []
    names = []

    print(f"코스닥 150 전체 {len(kosdaq150_codes)}개 항목 필터링 중...")

    # 3. 데이터 필터링 루프
    for code in kosdaq150_codes:
        # 필터 1: 주권(보통주)인지 확인 (1: 주권, 나머지는 신주인수권, ETF 등 제외)
        if objCodeMgr.GetStockSectionKind(code) != 1:
            continue
            
        # 필터 2: 종목코드 숫자 부분에 알파벳 포함 여부 확인 (A0126Z0 같은 특수종목 제외)
        if not code[1:].isdigit():
            continue
            
        # 필터 3: 우선주 제외 (이름 끝이 '우', '우B' 등으로 끝나는 경우)
        name = objCodeMgr.CodeToName(code)
        if name.endswith(('우', '우B', '우C', '우(전환)', '우선주')):
            continue

        codes.append(code)
        names.append(name)

    # 4. 데이터프레임 생성
    df = pd.DataFrame({
        '코드': codes,
        '종목 이름': names
    })

    # 5. 엑셀 파일로 저장
    filename = "KOSDAQ150_보통주_리스트.xlsx"
    df.to_excel(filename, index=False)
    
    print("-" * 30)
    print(f"필터링 후 최종 종목 수: {len(df)}개")
    print(f"저장 완료: {os.path.abspath(filename)}")
    print("-" * 30)
    
    # 6. 저장된 파일 즉시 열기
    os.startfile(filename)

if __name__ == "__main__":
    save_kosdaq150_common_stocks()