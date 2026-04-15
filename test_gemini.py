import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

from dotenv import find_dotenv
env_path = find_dotenv()
print(f"--- Đang load file .env từ: {env_path} ---")

api_key = os.getenv("GEMINI_API_KEY")
model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

print(f"--- Đang kiểm tra API Key: {api_key[:10]}...{api_key[-5:] if api_key else ''} ---")
print(f"--- Model: {model_name} ---")

if not api_key:
    print("[ERROR] Không tìm thấy GEMINI_API_KEY trong file .env")
else:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        response = model.generate_content("Hello, can you hear me?")
        print("[SUCCESS] API Key hoạt động bình thường!")
        print(f"Phản hồi từ Gemini: {response.text}")
    except Exception as e:
        print(f"[FAILED] Lỗi API: {e}")
