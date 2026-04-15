import os
import time
from pathlib import Path
import main as form_bot

LINKS_FILE = Path("links.txt")
PROCESSED_FILE = Path("processed_links.txt")

def load_links(file_path: Path) -> set:
    if not file_path.exists():
        return set()
    with open(file_path, "r", encoding="utf-8") as f:
        # Ignore empty lines and comments
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return set(lines)

def append_to_processed(url: str):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url}\n")

def run():
    print("========== BẮT ĐẦU CHẠY BULK FILL ==========")
    
    # Đọc các link cần làm và link đã làm
    pending_urls = load_links(LINKS_FILE)
    processed_urls = load_links(PROCESSED_FILE)
    
    # Lọc những link chưa làm
    urls_to_process = [url for url in pending_urls if url not in processed_urls]
    
    if not urls_to_process:
        print("[INFO] Không có link mới nào để chạy. Vui lòng thêm vào links.txt.")
        return

    print(f"[INFO] Tìm thấy {len(urls_to_process)} link cần xử lý.")
    
    success_count = 0
    fail_count = 0

    for idx, url in enumerate(urls_to_process, start=1):
        print(f"\n[{idx}/{len(urls_to_process)}] Đang xử lý: {url}")
        try:
            # Chạy pipeline của main.py
            success = form_bot.main(url=url)
            
            if success:
                print(f"[INFO] Thành công: {url}")
                append_to_processed(url)
                success_count += 1
            else:
                print(f"[ERROR] Thất bại: {url}")
                fail_count += 1
                
        except Exception as e:
            print(f"[ERROR] Lỗi không mong đợi khi xử lý {url}: {str(e)}")
            fail_count += 1
            
        print("-" * 50)
        time.sleep(2) # Nghỉ một lúc trước khi làm form tiếp theo

    print("========== TỔNG KẾT ==========")
    print(f"Tổng số form đã làm : {len(urls_to_process)}")
    print(f"Thành công          : {success_count}")
    print(f"Thất bại            : {fail_count}")

if __name__ == "__main__":
    if not LINKS_FILE.exists():
        with open(LINKS_FILE, "w", encoding="utf-8") as f:
            f.write("# dán link google form vào đây (mỗi link 1 dòng)\n")
    if not PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            f.write("# Danh sách các link đã hoàn thành (không sửa file này)\n")
    
    run()
