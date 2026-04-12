import schedule
import time
from datetime import datetime
import pytz
from uploader import upload_data

KST = pytz.timezone('Asia/Seoul')
EST = pytz.timezone('America/New_York')

def is_kr_market_open():
    now = datetime.now(KST)
    if now.weekday() >= 5:  # 주말
        return False
    hour = now.hour
    minute = now.minute
    current = hour * 60 + minute
    return 9 * 60 <= current <= 15 * 60 + 30  # 09:00~15:30

def is_us_market_open():
    now = datetime.now(EST)
    if now.weekday() >= 5:  # 주말
        return False
    hour = now.hour
    minute = now.minute
    current = hour * 60 + minute
    return 9 * 60 + 30 <= current <= 16 * 60  # 09:30~16:00

def run_if_market_open():
    if is_kr_market_open() or is_us_market_open():
        print(f"\n⏰ {datetime.now(KST).strftime('%H:%M')} - 장 열림, 업데이트 실행")
        upload_data()
    else:
        print(f"⏸️ {datetime.now(KST).strftime('%H:%M')} - 장 마감")

if __name__ == "__main__":
    print("🚀 주식 사여?! 스케줄러 시작")
    print("   국장: 09:00~15:30 KST")
    print("   미장: 09:30~16:00 EST (22:30~05:00 KST)")
    print("   주기: 10분\n")
    
    # 시작 시 1회 실행
    upload_data()
    
    # 10분마다 체크
    schedule.every(10).minutes.do(run_if_market_open)
    
    while True:
        schedule.run_pending()
        time.sleep(60)