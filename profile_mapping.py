"""Deterministic profile suggestions for Google Form completion."""

import re
from typing import Any, Mapping


_LABEL_PATTERNS = (
    (r"(?:mssv|mã số sinh viên|student id)", "mssv"),
    (r"(?:họ và tên|họ tên|full name)", "ho_ten"),
    (r"(?:sđt|số điện thoại|phone)", "sdt"),
    (r"(?:email|e-mail)", "email"),
    (r"(?:lớp|class)", "lop"),
    (r"(?:chuyên ngành|major)", "chuyen_nganh"),
    (r"(?:tên trường|trường đại học|university)", "truong"),
)


def match_profile_value(label: str, profile: Mapping[str, Any]) -> str | None:
    """Return a scalar profile value for an exact known Google Form label."""
    normalized = re.sub(r"\s+", " ", label.strip()).casefold().strip(" *:?.")
    key = next(
        (profile_key for pattern, profile_key in _LABEL_PATTERNS
         if re.fullmatch(pattern, normalized)),
        None,
    )
    value = profile.get(key) if key is not None else None
    if isinstance(value, str) and value.strip() and not value.casefold().startswith("your "):
        return value.strip()
    return None
