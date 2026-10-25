import os
import re
import discord
from dotenv import load_dotenv

load_dotenv(override=True)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")

class FormFillerClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print(f"[DISCORD] Đăng nhập thành công với tên: {self.user}")
        if DISCORD_CHANNEL_ID:
            print(f"[DISCORD] Đang lắng nghe tin nhắn tại kênh ID: {DISCORD_CHANNEL_ID}")
        else:
            print("[DISCORD] CẢNH BÁO: Chưa cấu hình DISCORD_CHANNEL_ID. Bot sẽ lắng nghe trên tất cả các kênh có quyền truy cập.")

    async def on_message(self, message):
        # Bỏ qua tin nhắn của chính mình
        if message.author == self.user:
            return

        # Nếu có cấu hình kênh cụ thể, bỏ qua tin nhắn từ kênh khác
        if DISCORD_CHANNEL_ID and str(message.channel.id) != DISCORD_CHANNEL_ID:
            return

        # Tìm link Google Form bằng Regex
        # Hỗ trợ dạng docs.google.com/forms/ và forms.gle/
        form_url_match = re.search(
            r"https://(?:docs\.google\.com/forms/(?:u/\d+/)?d/(?:e/)?[A-Za-z0-9_-]+/viewform"
            r"|forms\.gle/[A-Za-z0-9_-]+)(?:\?[^\s<>]*)?",
            message.content,
        )
        
        if form_url_match:
            form_url = form_url_match.group(0).rstrip(".,;:!?)]}>")
            print(f"\n[DISCORD] -------- PHÁT HIỆN LINK MỚI --------")
            print(f"URL: {form_url}")
            print(f"Từ: {message.author}")
            print(f"-------------------------------------------")

            # The supported workflow needs a person at the local browser and terminal.
            # Never run the synchronous browser workflow inside Discord's event loop.
            await message.reply(
                "Đã nhận link. Hãy thêm link vào links.txt trên máy chạy bot rồi chạy "
                "`python bulk_runner.py`. Luồng này chờ bạn đăng nhập và tự bấm Gửi trong trình duyệt."
            )


def run_bot():
    if not DISCORD_TOKEN:
        print("[ERROR] Chưa có DISCORD_TOKEN trong file .env!")
        return

    intents = discord.Intents.default()
    intents.message_content = True  # Cần cấp quyền đọc nội dung tin nhắn trên cổng Developer
    client = FormFillerClient(intents=intents)
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    run_bot()
    if not DISCORD_TOKEN:
        raise SystemExit(1)
