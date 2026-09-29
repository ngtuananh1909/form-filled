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
    "ho_ten": "",
    "mssv": "",
    "email": "",
    "sdt": "",
    "lop": "",
    "chuyen_nganh": "",
    "truong": "",
    "ky_nang": [],
    "du_an": [],
    "muc_tieu": "",
}
