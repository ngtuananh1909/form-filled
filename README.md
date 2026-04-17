
# form-filled

## Hướng dẫn sử dụng

1. **Sao chép file mẫu:**
	 - Tạo file chứa link cần điền:
		 ```sh
		 cp links_example.txt links.txt
		 ```
	 - Tạo file cấu hình thông tin cá nhân:
		 ```sh
		 cp config_example.py config.py
		 ```
		 > Sửa file `config.py` với thông tin của bạn.
	 - Tạo file cấu hình API Gemini:
		 ```sh
		 cp .env_example .env
		 ```
		 > Điền API key và model vào file `.env`.

2. **Tạo môi trường ảo:**
	 - Trên Windows:
		 ```sh
		 python -m venv venv
		 venv\Scripts\activate
		 ```
	 - Trên Unix/Linux/MacOS:
		 ```sh
		 python3 -m venv venv
		 source venv/bin/activate
		 ```

3. **Cài đặt thư viện:**
	 ```sh
	 pip install -r requirements.txt
	 ```

4. **Cài đặt Chromium cho Playwright:**
	 ```sh
	 playwright install chromium
	 ```

5. **Chạy script điền form:**
	 ```sh
	 python bulk_fill.py
	 ```

---

## Ghi chú
- File `links.txt` chứa các link form cần điền.
- File `config.py` chứa thông tin cá nhân, cần chỉnh sửa cho phù hợp.
- File `.env` chứa thông tin API Gemini.

---

## Troubleshooting
- Nếu gặp lỗi về thiếu thư viện, kiểm tra đã kích hoạt đúng môi trường ảo chưa.
- Nếu gặp lỗi về trình duyệt, đảm bảo đã chạy `playwright install chromium`.

---

## Liên hệ
Nếu cần hỗ trợ, hãy tạo issue hoặc liên hệ qua email [nta1909k7@gmail.com].
