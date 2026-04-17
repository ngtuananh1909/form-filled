import os
from dataclasses import dataclass
from typing import Dict, Any

from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass(frozen=True)
class AIConfig:
    model: str = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    api_base: str = os.getenv("GEMINI_API_BASE", "")


USER_PROFILE: Dict[str, Any] = {
    "ho_ten": "Your Name",
    "mssv": "Your Student ID",
    "email": "Your Email",
    "sdt": "Your Phone Number",
    "lop": "Your Class",
    "chuyen_nganh": "Your Major",
    "truong": "Your University",
    "ky_nang": [
        "Python",
        "Playwright",
        "Automation testing",
        "Web development",
        "CI/CD",
    ],
    "du_an": [
        "Tự động hóa kiểm thử web với Playwright",
        "Xây dựng pipeline kiểm thử cho ứng dụng nội bộ",
        "Bot thu thập và xử lý dữ liệu biểu mẫu",
    ],
    "muc_tieu": "Trả lời biểu mẫu chính xác, ngắn gọn, phù hợp bối cảnh sinh viên ngành KTPM.",
}

AI_CONFIG = AIConfig()
