import os
import re
import discord
from dotenv import load_dotenv

import main as form_bot

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
        form_url_match = re.search(r"(https?://(?:docs\.google\.com/forms/d/e/|forms\.gle/)[^\s]+)", message.content)
        
        if form_url_match:
            form_url = form_url_match.group(1)
            print(f"\n[DISCORD] -------- PHÁT HIỆN LINK MỚI --------")
            print(f"URL: {form_url}")
            print(f"Từ: {message.author}")
            print(f"-------------------------------------------")

            # Báo cho người dùng biết bot đang bắt đầu xử lý
            status_msg = await message.reply("🤖 **Đã nhận link Google Form!** Đang tiến hành phân tích và tự động điền...")
            
            # Gửi lên một luồng chạy ngầm để không chặn Discord bot
            success = False
            try:
                # Gọi hàm main.py
                success = form_bot.main(url=form_url)
            except Exception as e:
                print(f"[DISCORD] Lỗi khi chạy bot điền form: {e}")
                
            if success:
                try:
                    # Gửi tin nhắn thành công kèm ảnh chụp màn hình
                    await status_msg.edit(content="✅ **Thành công!** Form đã được điền và tự động nộp.")
                    if os.path.exists("form_filled_preview.png"):
                        file = discord.File("form_filled_preview.png", filename="preview.png")
                        await message.channel.send(file=file)
                except Exception as e:
                    print(f"[DISCORD] Không thể gửi ảnh: {e}")
            else:
                await status_msg.edit(content="❌ **Thất bại!** Có lỗi xảy ra trong quá trình tự động điền form. Vui lòng kiểm tra log hệ thống.")


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
