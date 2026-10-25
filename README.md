# form-filled

Điền Google Form theo danh sách, **để bạn tự kiểm tra và bấm Gửi**. Bot chỉ chuyển sang link tiếp theo sau khi nhìn thấy trang xác nhận phản hồi.

## Cài đặt

Các lệnh sau dành cho Linux/macOS; cách kích hoạt môi trường trên Windows ở phần ghi chú bên dưới.

```sh
[ -e links.txt ] || cp links_example.txt links.txt
[ -e config.py ] || cp config_example.py config.py
[ -e .env ] || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Sửa `config.py` bằng thông tin của bạn, chọn một provider trong `.env`, và thêm mỗi link Google Form vào một dòng `links.txt`. Nếu dùng Gemini, `GEMINI_MODEL` để trống sẽ dùng `gemini-flash-latest`; bạn có thể ghim một model ổn định theo [danh sách model của Google](https://ai.google.dev/gemini-api/docs/models). Không đưa dữ liệu cá nhân hoặc `user_data/` lên Git; lưu ý `config.py` và `links.txt` hiện đã được Git theo dõi nên `.gitignore` không bảo vệ chúng. `HEADLESS` phải là `false` để bạn đăng nhập và bấm Gửi.

## Dùng AI local theo API tương thích OpenAI

Nếu server của bạn cung cấp endpoint `POST /v1/chat/completions`, thêm hoặc sửa các dòng này trong **`.env` hiện có** (chỉnh `.env.example` không tự đổi cấu hình đang chạy):

```sh
AI_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:1234/v1
OPENAI_COMPATIBLE_API_KEY=
OPENAI_COMPATIBLE_MODEL=ten-model-tren-server-cua-ban
```

Thay địa chỉ và tên model đúng theo server của bạn. Nếu server yêu cầu Bearer token, điền `OPENAI_COMPATIBLE_API_KEY`; nếu không yêu cầu, để trống. Base URL cũng có thể là `http://localhost:11434` (công cụ thêm `/v1/chat/completions`) hoặc URL đầy đủ kết thúc bằng `/chat/completions`. Provider này gọi đúng địa chỉ bạn cấu hình và không chọn model của OpenAI. Chạy `python test_gemini.py` để kiểm tra một câu hỏi giả lập không chứa hồ sơ cá nhân trước khi mở form.

## AI provider miễn phí đề xuất

OpenRouter với `nvidia/nemotron-3-ultra-550b-a55b:free` vẫn là lựa chọn khác nếu đặt `AI_PROVIDER=openrouter`. Tạo key tại [OpenRouter Keys](https://openrouter.ai/keys), rồi đặt các dòng sau trong `.env`:

```sh
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=dán_key_của_bạn_vào_đây
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
AI_BATCH_SIZE=8
AI_RETRY_ATTEMPTS=5
AI_RETRY_BASE_SECONDS=3
```

Bot gọi OpenRouter bằng HTTP chuẩn, không cần cài thêm thư viện. Model `:free` có quota thấp và có thể tạm hết provider; kiểm tra mức quota thực tế trong OpenRouter. Khi API tạm quá tải, bot chia form thành nhóm 8 câu, retry có backoff. Nếu API vẫn không phục vụ được, lượt chạy dừng với một lỗi AI rõ ràng và không chuyển câu hỏi sang yêu cầu bạn chọn thủ công. Gemini vẫn khả dụng nếu đặt `AI_PROVIDER=gemini`.

Gemini là lựa chọn thay thế. Tạo key tại [Google AI Studio](https://aistudio.google.com/apikey), rồi đặt provider và key trong `.env`:

```sh
AI_PROVIDER=gemini
GEMINI_API_KEY=dán_key_của_bạn_vào_đây
GEMINI_MODEL=gemini-flash-lite-latest
```

`GEMINI_API_BASE` chỉ cần đặt khi bạn dùng một endpoint Gemini tương thích khác. Chọn đúng provider trước khi chạy; `test_gemini.py` kiểm tra provider đang cấu hình, nên dùng được cho cả OpenRouter và Gemini.

Trên Windows, tạo môi trường bằng `python -m venv .venv` rồi chạy `.venv\Scripts\activate`. Trong mọi trường hợp, hãy dùng `python -m playwright ...` từ đúng môi trường ảo sau khi cài hoặc cập nhật Playwright.

## Chạy và tiếp tục

```sh
python bulk_runner.py
```

1. Bot mở một trình duyệt Chromium với phiên đăng nhập lưu trong `user_data/`. Nếu chưa vào được form, hãy đăng nhập tài khoản SV ngay trong trình duyệt. Bot chờ cho đến khi form xuất hiện; `Ctrl+C` để hủy.
2. Bot điền những câu có đáp án hợp lệ. Nếu câu chưa chắc chắn, chưa hỗ trợ, form báo lỗi hoặc cần xác nhận dùng email, hãy sửa/chọn trên trang rồi **nhấn Enter ở terminal** để kiểm tra và tiếp tục.
3. Ở trang cuối, bot dừng trước nút **Gửi**. Hãy tự xem lại và bấm Gửi. Khi trang xác nhận xuất hiện, bot mới ghi `confirmed` và mở link tiếp theo.

Các câu radio, checkbox và dropdown được gửi tới AI bằng choice ID nội bộ, do đó model chọn đúng lựa chọn trên form thay vì tự viết đáp án. Nếu AI không trả được choice ID hợp lệ, terminal sẽ ghi rõ nguyên nhân để bạn biết đây là lỗi AI/dữ liệu form, thay vì chỉ báo chung là “Chưa chọn đáp án”.

Nếu đã bấm Gửi nhưng bot không nhận ra trang xác nhận, bot dừng ở trạng thái `unknown` và không tự gửi lại. Kiểm tra phản hồi thủ công trước khi giải quyết trạng thái:

```sh
python bulk_runner.py --list-status
python bulk_runner.py --resolve-url 'https://docs.google.com/forms/d/e/FORM_ID/viewform' --as confirmed
# Chỉ dùng queued khi bạn chắc chắn form chưa được gửi:
python bulk_runner.py --resolve-url 'https://docs.google.com/forms/d/e/FORM_ID/viewform' --as queued
```

Lần chạy đầu nhập các link từ `processed_links.txt` cũ thành `legacy_unverified`, vì phiên bản cũ có thể ghi thành công khi chưa gửi. Hãy kiểm tra từng link rồi dùng lệnh `--resolve-url` tương ứng. Cơ sở dữ liệu trạng thái `form_runs.sqlite3` chỉ nằm trên máy và được bỏ qua bởi Git.

Luồng chính chỉ hỗ trợ `accuracy` với một lần xử lý cho mỗi link. `discord_bridge.py` chỉ thông báo cách chạy cục bộ (cài riêng bằng `python -m pip install -r requirements-discord.txt`); userscript gửi lặp là công cụ riêng, không thuộc workflow an toàn này.

## Chromium trên Arch Linux

Playwright chỉ liệt kê Ubuntu/Debian trong các bản Linux được hỗ trợ chính thức. Trên Arch, **cài browser do đúng phiên bản Playwright trong `.venv` quản lý**:

```sh
python -m playwright install chromium
python -m playwright install --list
```

Có thể kiểm tra Chromium mở được trong phiên desktop:

```sh
python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    print("Chromium OK:", browser.version)
    browser.close()
PY
```

Nếu tải chậm, thử `PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000 python -m playwright install chromium`. Cảnh báo dùng bản build Linux dự phòng trên Arch không nhất thiết là lỗi; lệnh mở browser ở trên mới kiểm tra khả năng chạy thực tế. **Không dùng** `python -m playwright install --with-deps chromium` trên Arch: nó có thể gọi `apt-get`. Nếu browser báo thiếu thư viện `.so`, tìm gói Arch chứa thư viện đó bằng `sudo pacman -Fy` và `pacman -F ten-thu-vien.so`, rồi cài gói được báo bằng `sudo pacman -S --needed ten-goi`. Nếu báo lỗi quyền sandbox (`Operation not permitted`), kiểm tra lại trên phiên desktop thông thường; lỗi này khác với thiếu thư viện.

## Kiểm thử

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Test tự động không cần API key hay gửi Google Form thật. Kiểm tra đăng nhập SV và gửi thủ công trên Google Forms thật chỉ nên thực hiện với form thử riêng.

Sau khi đặt API key của provider đã chọn (`OPENROUTER_API_KEY` hoặc `GEMINI_API_KEY`), kiểm tra provider trước khi chạy form:

```sh
python test_gemini.py
```

Nguồn: [Playwright Python](https://playwright.dev/python/docs/intro), [Playwright browsers](https://playwright.dev/python/docs/browsers), [vấn đề Arch trong Playwright](https://github.com/microsoft/playwright/issues/30393).
