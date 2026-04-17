import argparse
import os
import time
from pathlib import Path
import main as form_bot

LINKS_FILE = Path("links.txt")
PROCESSED_FILE = Path("processed_links.txt")

def load_links(file_path: Path) -> list:
    if not file_path.exists():
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return lines

def load_processed() -> set:
    if not PROCESSED_FILE.exists():
        return set()
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}

def append_to_processed(url: str):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url}\n")

def run(mode: str, repeat: int = 1):
    print("=" * 50)
    if mode == "spam":
        print(f"🚀 CHẾ ĐỘ SPAM - Lặp {repeat} lần cho mỗi link")
    else:
        print("🎯 CHẾ ĐỘ ACCURACY - Dùng Gemini AI")
    print("=" * 50)

    pending_urls = load_links(LINKS_FILE)
    
    if mode == "accuracy":
        processed_urls = load_processed()
        urls_to_process = [url for url in pending_urls if url not in processed_urls]
    else:
        # Spam mode: chạy tất cả, không cần kiểm tra đã làm chưa
        urls_to_process = pending_urls

    if not urls_to_process:
        print("[INFO] Không có link nào để chạy. Thêm vào links.txt.")
        return

    total_runs = len(urls_to_process) * repeat
    print(f"[INFO] {len(urls_to_process)} link x {repeat} lần = {total_runs} phiên tổng cộng")
    
    success_count = 0
    fail_count = 0

    for r in range(repeat):
        if repeat > 1:
            print(f"\n{'='*50}")
            print(f"  VÒNG LẶP {r+1}/{repeat}")
            print(f"{'='*50}")

        for idx, url in enumerate(urls_to_process, start=1):
            run_label = f"[Vòng {r+1} - {idx}/{len(urls_to_process)}]" if repeat > 1 else f"[{idx}/{len(urls_to_process)}]"
            print(f"\n{run_label} Đang xử lý: {url}")
            try:
                success = form_bot.main(url=url, mode=mode)
                
                if success:
                    print(f"[OK] Thành công: {url}")
                    if mode == "accuracy":
                        append_to_processed(url)
                    success_count += 1
                else:
                    print(f"[FAIL] Thất bại: {url}")
                    fail_count += 1
                    
            except Exception as e:
                print(f"[FAIL] Lỗi: {str(e)}")
                fail_count += 1
                
            print("-" * 50)
            if mode != "spam":
                time.sleep(2)

    print("\n" + "=" * 50)
    print("  TỔNG KẾT")
    print("=" * 50)
    print(f"  Tổng phiên chạy   : {success_count + fail_count}")
    print(f"  Thành công         : {success_count}")
    print(f"  Thất bại           : {fail_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk Google Form Filler")
    parser.add_argument(
        "--mode", 
        choices=["accuracy", "spam"], 
        default=os.getenv("RUN_MODE", "accuracy"),
        help="Chế độ: accuracy (AI, chính xác) hoặc spam (ngẫu nhiên, nhanh)"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Số lần lặp spam cho mỗi link (chỉ có ý nghĩa ở mode spam)"
    )
    args = parser.parse_args()
    
    if not LINKS_FILE.exists():
        with open(LINKS_FILE, "w", encoding="utf-8") as f:
            f.write("# dán link google form vào đây (mỗi link 1 dòng)\n")
    if not PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            f.write("# Danh sách các link đã hoàn thành (không sửa file này)\n")
    
    run(mode=args.mode, repeat=args.repeat)
