"""Fill a Google Form, then wait for a person to submit it."""

import json
import os
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlparse, urlunparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import google.generativeai as genai
from google.api_core import exceptions as google_errors
from google.generativeai import types as genai_types
from playwright.sync_api import BrowserContext, Locator, Page, Playwright, sync_playwright

from config import AI_CONFIG, USER_PROFILE
from profile_mapping import match_profile_value


FORM_URL = os.getenv("FORM_URL", "")
PROJECT_DIR = Path(__file__).resolve().parent
_user_data_setting = Path(os.getenv("USER_DATA_DIR", "./user_data"))
USER_DATA_DIR = _user_data_setting if _user_data_setting.is_absolute() else PROJECT_DIR / _user_data_setting
HEADLESS = os.getenv("HEADLESS", "false").strip().lower() == "true"
GEMINI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", os.getenv("GEMINI_TIMEOUT_SECONDS", "60")))
AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter").strip().lower()
AI_BATCH_SIZE = max(1, int(os.getenv("AI_BATCH_SIZE", "8")))
AI_RETRY_ATTEMPTS = max(1, int(os.getenv("AI_RETRY_ATTEMPTS", "5")))
AI_RETRY_BASE_SECONDS = max(0.1, float(os.getenv("AI_RETRY_BASE_SECONDS", "3")))
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "60"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
OPENAI_COMPATIBLE_BASE_URL = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "")
OPENAI_COMPATIBLE_API_KEY = os.getenv("OPENAI_COMPATIBLE_API_KEY", "")
OPENAI_COMPATIBLE_MODEL = os.getenv("OPENAI_COMPATIBLE_MODEL", "")
MAX_PAGES = 25
CONFIRMATION_GRACE_SECONDS = 15
StateCallback = Callable[[str, str], None]
InputCallback = Callable[[str], str]


class RunStatus(str, Enum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    url: str
    reason: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status == RunStatus.CONFIRMED


def validate_form_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    short_form = host == "forms.gle" and re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path)
    long_form = host == "docs.google.com" and re.fullmatch(
        r"/forms/(?:u/\d+/)?d/(?:e/)?[A-Za-z0-9_-]+/viewform/?", parsed.path
    )
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or not (short_form or long_form)):
        raise ValueError("Chỉ chấp nhận URL HTTPS của Google Forms hoặc forms.gle")
    return url.strip()


def launch_context(playwright: Playwright) -> BrowserContext:
    if HEADLESS:
        raise RuntimeError("Luồng gửi thủ công cần HEADLESS=false")
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=False,
        viewport={"width": 1400, "height": 900},
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _first_line(value: str) -> str:
    return _normalize_text((value or "").splitlines()[0] if value else "")


def extract_question_label(node: Locator) -> str:
    for selector in ('[role="heading"]', '[data-params*="question"]'):
        candidate = node.locator(selector).first
        if candidate.count():
            try:
                text = _normalize_text(candidate.inner_text(timeout=500))
                if text:
                    return text
            except Exception:
                pass
    try:
        return _first_line(node.inner_text(timeout=1000))
    except Exception:
        return ""


def extract_row_label(node: Locator) -> str:
    row_header = node.locator('[role="rowheader"]').first
    if row_header.count():
        return _normalize_text(row_header.inner_text(timeout=500))
    return _first_line(node.inner_text(timeout=500))


def extract_option_text(node: Locator) -> str:
    return _normalize_text(node.get_attribute("aria-label") or node.inner_text(timeout=500))


def _options(node: Locator, qtype: str) -> list[str]:
    role = "option" if qtype == "dropdown" else qtype
    roles = node.locator(f'[role="{role}"]')
    options = [extract_option_text(roles.nth(i)) for i in range(roles.count())]
    if qtype == "dropdown":
        return [option for option in options if not _is_placeholder_option(option)]
    return options


def _is_placeholder_option(value: str) -> bool:
    return _normalize_text(value).casefold() in {"chọn", "choose", "select", "select an option"}


def _choices(options: Sequence[str]) -> list[dict[str, str]]:
    """Stable, short IDs keep Gemini from paraphrasing a visible option label."""
    return [{"id": f"o{index}", "label": option} for index, option in enumerate(options)]


def _question_type(node: Locator) -> str:
    # "Other" text inputs can coexist with radio/checkbox controls.
    if node.locator('[role="radio"]').count():
        return "radio"
    if node.locator('[role="checkbox"]').count():
        return "checkbox"
    if node.locator('[role="listbox"] [role="option"]').count():
        return "dropdown"
    if node.locator('textarea, input[type="text"], input[type="email"], input[type="number"], input:not([type])').count():
        return "text"
    return "unsupported"


def parse_form_questions(page: Page, page_number: int) -> list[dict[str, Any]]:
    # Google Forms can add questions while scrolling.
    previous = -1
    for _ in range(5):
        count = page.locator('div[role="listitem"]').count()
        if count == previous:
            break
        previous = count
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(150)

    items = page.locator('div[role="listitem"]')
    questions: list[dict[str, Any]] = []
    for i in range(items.count()):
        node = items.nth(i)
        label = extract_question_label(node) or f"Câu {i + 1}"
        rows = node.locator('div[role="row"]')
        grid_rows = [r for r in range(rows.count())
                     if rows.nth(r).locator('[role="rowheader"]').count()
                     and rows.nth(r).locator('[role="radio"], [role="checkbox"]').count()]
        if grid_rows:
            for r in grid_rows:
                row = rows.nth(r)
                qtype = _question_type(row)
                options = _options(row, qtype)
                questions.append({
                    "id": f"p{page_number}.q{i}.r{r}", "index": i, "row_index": r,
                    "label": f"{label} — {extract_row_label(row) or f'Hàng {r + 1}'}",
                    "type": qtype, "options": options,
                    "choices": _choices(options),
                })
            continue
        qtype = _question_type(node)
        if qtype == "unsupported" and not node.locator('input, textarea, [role="listbox"], [role="radio"], [role="checkbox"]').count():
            # A section description can also be represented as a listitem.
            continue
        options = _options(node, qtype) if qtype in {"radio", "checkbox", "dropdown"} else []
        questions.append({
            "id": f"p{page_number}.q{i}", "index": i, "row_index": None,
            "label": label, "type": qtype,
            "options": options, "choices": _choices(options),
        })
    return questions


def _question_node(page: Page, question: dict[str, Any]) -> Locator:
    node = page.locator('div[role="listitem"]').nth(question["index"])
    if question["row_index"] is not None:
        node = node.locator('div[role="row"]').nth(question["row_index"])
    return node


def build_ai_prompt(questions: Sequence[dict[str, Any]]) -> str:
    payload = [{
        "id": q["id"], "label": q["label"], "type": q["type"],
        "choices": q.get("choices") or _choices(q.get("options", [])),
    } for q in questions]
    return (
        "Bạn là hệ thống chọn đáp án cho Google Form. Trả về DUY NHẤT JSON object. "
        "Mỗi khóa phải là đúng question id. Text/radio là string; checkbox là mảng string. "
        "Với radio/checkbox/dropdown, giá trị PHẢI là choice id (ví dụ o0), không phải nhãn, "
        "không phải nội dung tự viết. Chọn một choice id có trong choices cho mọi câu trắc nghiệm. "
        "Nếu không chắc chắn, trả null; không suy đoán hoặc tạo thông tin cá nhân. "
        "Không bỏ khóa câu hỏi.\nUSER_PROFILE:\n{}"
        + "\nQUESTIONS:\n" + json.dumps(payload, ensure_ascii=False)
    )


def profile_answers(questions: Sequence[dict[str, Any]]) -> dict[str, str]:
    known: dict[str, str] = {}
    for q in questions:
        if q["type"] != "text":
            continue
        value = match_profile_value(q["label"], USER_PROFILE)
        if value is not None:
            known[q["id"]] = value
    return known


def _generate_with_retry(model: Any, prompt: str) -> Any:
    transient = (google_errors.ServiceUnavailable, google_errors.DeadlineExceeded,
                 google_errors.InternalServerError)
    for attempt in range(AI_RETRY_ATTEMPTS):
        try:
            return model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
                request_options=genai_types.RequestOptions(timeout=GEMINI_TIMEOUT_SECONDS),
            )
        except transient:
            if attempt == AI_RETRY_ATTEMPTS - 1:
                raise
            delay = AI_RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
            print(f"[AI] Gemini tạm quá tải; thử lại sau {delay:.1f} giây ({attempt + 1}/{AI_RETRY_ATTEMPTS}).")
            time.sleep(delay)
    raise AssertionError("unreachable")


def ask_gemini(questions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not AI_CONFIG.api_key:
        raise RuntimeError("Thiếu GEMINI_API_KEY")
    kwargs: dict[str, Any] = {"api_key": AI_CONFIG.api_key}
    if AI_CONFIG.api_base:
        kwargs["client_options"] = {"api_endpoint": AI_CONFIG.api_base}
    genai.configure(**kwargs)
    model = genai.GenerativeModel(AI_CONFIG.model.strip() or "gemini-flash-latest")
    response = _generate_with_retry(model, build_ai_prompt(questions))
    parsed = json.loads((response.text or "").strip())
    if not isinstance(parsed, dict):
        raise ValueError("Gemini không trả JSON object")
    return parsed


def _extract_json_object(value: str, provider: str) -> dict[str, Any]:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError(f"{provider} không trả JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError(f"{provider} không trả JSON object")
    return parsed


def ask_openrouter(questions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("Thiếu OPENROUTER_API_KEY trong .env")
    request_body = json.dumps({
        "model": OPENROUTER_MODEL,
        "temperature": 0,
        "messages": [{"role": "user", "content": build_ai_prompt(questions)}],
    }).encode("utf-8")
    last_error: Optional[Exception] = None
    for attempt in range(AI_RETRY_ATTEMPTS):
        request = Request(
            "https://openrouter.ai/api/v1/chat/completions", data=request_body,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=AI_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            text = payload["choices"][0]["message"]["content"]
            return _extract_json_object(text, "OpenRouter")
        except HTTPError as exc:
            # Provider error bodies may echo prompts or credentials.
            last_error = RuntimeError(f"OpenRouter HTTP {exc.code}")
            retryable = exc.code in {408, 429, 500, 502, 503, 504}
        except (URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = exc
            retryable = True
        if not retryable or attempt == AI_RETRY_ATTEMPTS - 1:
            raise last_error
        delay = AI_RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
        print(f"[AI] OpenRouter tạm không phản hồi; thử lại sau {delay:.1f} giây ({attempt + 1}/{AI_RETRY_ATTEMPTS}).")
        time.sleep(delay)
    raise last_error or RuntimeError("OpenRouter không phản hồi")


def _compatible_chat_url(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.params):
        raise ValueError("OPENAI_COMPATIBLE_BASE_URL phải là URL http(s) của server AI")
    # Accessing parsed.port also rejects malformed ports before any request is sent.
    _ = parsed.port
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    elif not path:
        endpoint_path = "/v1/chat/completions"
    else:
        endpoint_path = path + "/chat/completions"
    return urlunparse((parsed.scheme, parsed.netloc, endpoint_path, "", "", ""))


def ask_openai_compatible(questions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    endpoint = _compatible_chat_url(OPENAI_COMPATIBLE_BASE_URL)
    model = OPENAI_COMPATIBLE_MODEL.strip()
    if not model:
        raise RuntimeError("Thiếu OPENAI_COMPATIBLE_MODEL trong .env")
    headers = {"Content-Type": "application/json"}
    if OPENAI_COMPATIBLE_API_KEY:
        headers["Authorization"] = f"Bearer {OPENAI_COMPATIBLE_API_KEY}"
    request_body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": build_ai_prompt(questions)}],
    }).encode("utf-8")
    last_error: Optional[Exception] = None
    for attempt in range(AI_RETRY_ATTEMPTS):
        request = Request(endpoint, data=request_body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=AI_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            return _extract_json_object(content, "OpenAI-compatible")
        except HTTPError as exc:
            last_error = RuntimeError(f"OpenAI-compatible HTTP {exc.code}")
            retryable = exc.code in {408, 429, 500, 502, 503, 504}
        except (URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = exc
            retryable = True
        if not retryable or attempt == AI_RETRY_ATTEMPTS - 1:
            raise last_error
        delay = AI_RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
        print(f"[AI] Server local tạm không phản hồi; thử lại sau {delay:.1f} giây ({attempt + 1}/{AI_RETRY_ATTEMPTS}).")
        time.sleep(delay)
    raise last_error or RuntimeError("Server local không phản hồi")


def ask_ai(questions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if AI_PROVIDER == "openrouter":
        return ask_openrouter(questions)
    if AI_PROVIDER == "openai_compatible":
        return ask_openai_compatible(questions)
    if AI_PROVIDER == "gemini":
        return ask_gemini(questions)
    raise RuntimeError("AI_PROVIDER chỉ hỗ trợ openrouter, openai_compatible hoặc gemini")


def ask_ai_in_batches(questions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    batches = [questions[start:start + AI_BATCH_SIZE] for start in range(0, len(questions), AI_BATCH_SIZE)]
    for index, batch in enumerate(batches, 1):
        print(f"[AI] Đang xử lý nhóm {index}/{len(batches)} ({len(batch)} câu).")
        response = ask_ai(batch)
        normalized, ignored = normalize_answer_keys(batch, response)
        if ignored:
            print(f"[AI] Bỏ qua {len(ignored)} khóa không khớp ở nhóm {index}/{len(batches)}.")
        answers.update(normalized)
    return answers


def _safe_ai_error(exc: Exception) -> str:
    message = str(exc)
    if message in {"Thiếu GEMINI_API_KEY", "Thiếu OPENROUTER_API_KEY trong .env",
                   "Thiếu OPENAI_COMPATIBLE_MODEL trong .env"}:
        return message
    if re.fullmatch(r"(?:OpenRouter|OpenAI-compatible) HTTP \d{3}", message):
        return message
    return type(exc).__name__


def normalize_answer_keys(questions: Sequence[dict[str, Any]], answers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Accept legacy Gemini replies keyed by a unique visible label.

    Gemini occasionally returns question labels despite an ID-only instruction. A
    label is accepted only when it identifies exactly one parsed question.
    """
    question_ids = {q["id"] for q in questions}
    labels: dict[str, list[str]] = {}
    for q in questions:
        labels.setdefault(_normalize_text(q["label"]).casefold(), []).append(q["id"])

    normalized: dict[str, Any] = {}
    ignored: list[str] = []
    for raw_key, value in answers.items():
        key = str(raw_key)
        if key in question_ids:
            normalized[key] = value
            continue
        matching_ids = labels.get(_normalize_text(key).casefold(), [])
        if len(matching_ids) == 1 and matching_ids[0] not in normalized:
            normalized[matching_ids[0]] = value
        else:
            ignored.append(key)
    return normalized, ignored


def _choice_label(question: dict[str, Any], answer: str) -> Optional[str]:
    choices = question.get("choices") or _choices(question.get("options", []))
    target = _normalize_text(answer).casefold()
    id_matches = [choice["label"] for choice in choices if choice["id"].casefold() == target]
    if len(id_matches) == 1:
        return id_matches[0]

    # Compatibility with older prompts that asked Gemini to return the label.
    label_matches = [choice["label"] for choice in choices
                     if _normalize_text(choice["label"]).casefold() == target]
    return label_matches[0] if len(label_matches) == 1 else None


def validate_answers(questions: Sequence[dict[str, Any]], answers: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    accepted: dict[str, Any] = {}
    issues: dict[str, str] = {}
    for q in questions:
        key, kind = q["id"], q["type"]
        value = answers.get(key)
        if kind == "unsupported":
            issues[key] = "Kiểu câu hỏi chưa được hỗ trợ"
        elif kind == "text":
            if isinstance(value, str) and value.strip() and value.strip().casefold() not in {"n/a", "ok", "không có thông tin", "không biết"}:
                accepted[key] = value.strip()
            else:
                issues[key] = "Thiếu câu trả lời text đáng tin cậy"
        elif kind in {"radio", "dropdown"}:
            option = _choice_label(q, value) if isinstance(value, str) else None
            if option:
                accepted[key] = option
            else:
                issues[key] = f"AI không trả choice id/lựa chọn hợp lệ cho {kind}"
        elif kind == "checkbox":
            if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
                issues[key] = "Checkbox cần mảng lựa chọn không rỗng"
                continue
            options = [_choice_label(q, item) for item in value]
            if None in options or len(set(options)) != len(options):
                issues[key] = "Gemini trả choice id/lựa chọn checkbox sai hoặc trùng"
            else:
                accepted[key] = options
    return accepted, issues


def _find_option(node: Locator, role: str, value: str) -> Optional[Locator]:
    actual_role = "option" if role == "dropdown" else role
    roles = node.locator(f'[role="{actual_role}"]')
    matches = [roles.nth(i) for i in range(roles.count()) if _normalize_text(extract_option_text(roles.nth(i))).casefold() == _normalize_text(value).casefold()]
    return matches[0] if len(matches) == 1 else None


def _is_selected(option: Locator, qtype: str) -> bool:
    attribute = "aria-selected" if qtype == "dropdown" else "aria-checked"
    return option.get_attribute(attribute) == "true"


def _selected_option_texts(node: Locator, qtype: str) -> list[str]:
    role = "option" if qtype == "dropdown" else qtype
    options = node.locator(f'[role="{role}"]')
    return [extract_option_text(options.nth(i)) for i in range(options.count())
            if _is_selected(options.nth(i), qtype)
            and (qtype != "dropdown" or not _is_placeholder_option(extract_option_text(options.nth(i))))]


def fill_form(page: Page, questions: Sequence[dict[str, Any]], answers: dict[str, Any]) -> dict[str, str]:
    issues: dict[str, str] = {}
    for q in questions:
        if q["id"] not in answers:
            continue
        node = _question_node(page, q)
        value = answers[q["id"]]
        try:
            if q["type"] == "text":
                node.locator('textarea, input:not([type="hidden"])').first.fill(value)
            elif q["type"] in {"radio", "dropdown"}:
                option = _find_option(node, q["type"], value)
                if option is None:
                    raise ValueError(f"Không tìm thấy lựa chọn {q['type']}")
                if not _is_selected(option, q["type"]):
                    option.click()
            elif q["type"] == "checkbox":
                options = node.locator('[role="checkbox"]')
                selected = {_normalize_text(item).casefold() for item in value}
                for i in range(options.count()):
                    option = options.nth(i)
                    should_check = _normalize_text(extract_option_text(option)).casefold() in selected
                    is_checked = _is_selected(option, "checkbox")
                    if should_check != is_checked:
                        option.click()
            if q["type"] in {"radio", "checkbox", "dropdown"}:
                selected_now = _selected_option_texts(node, q["type"])
                expected = [value] if q["type"] in {"radio", "dropdown"} else value
                if {_normalize_text(item).casefold() for item in selected_now} != {_normalize_text(item).casefold() for item in expected}:
                    raise ValueError("Trạng thái lựa chọn sau khi điền không khớp đáp án")
        except Exception as exc:
            issues[q["id"]] = f"Không điền được: {exc}"
    return issues


def inspect_questions(page: Page, questions: Sequence[dict[str, Any]], manual_ack: set[str]) -> dict[str, str]:
    issues: dict[str, str] = {}
    for q in questions:
        key, kind = q["id"], q["type"]
        node = _question_node(page, q)
        if kind == "unsupported":
            if key not in manual_ack:
                issues[key] = "Cần bạn tự điền và xác nhận"
        elif kind == "text":
            field = node.locator('textarea, input:not([type="hidden"])').first
            if not field.count() or not field.input_value().strip():
                issues[key] = "Text còn trống"
            elif not field.evaluate("el => el.checkValidity ? el.checkValidity() : true"):
                issues[key] = "Giá trị text không đúng định dạng"
        elif kind in {"radio", "checkbox", "dropdown"}:
            if not _selected_option_texts(node, kind):
                issues[key] = "Chưa chọn đáp án"
    return issues


def merge_issues(primary: dict[str, str], additional: dict[str, str]) -> dict[str, str]:
    """Keep the original AI validation error instead of hiding it as 'unselected'."""
    for key, reason in additional.items():
        primary.setdefault(key, reason)
    return primary


def _page_issues(page: Page) -> dict[str, str]:
    issues: dict[str, str] = {}
    # Google Forms may require consent to record the signed-in email above the
    # normal listitems. The person must choose this checkbox themselves.
    email_consent = page.locator('[data-user-email-address] [role="checkbox"]')
    if email_consent.count() and not any(
        email_consent.nth(i).get_attribute("aria-checked") == "true"
        for i in range(email_consent.count())
    ):
        issues["email_consent"] = "Chọn ô xác nhận dùng email ở đầu form"
    return issues


def _visible_error(page: Page) -> bool:
    errors = page.locator('[role="alert"], [aria-invalid="true"], [class*="ErrorMessage"]')
    return any(errors.nth(i).is_visible() for i in range(min(errors.count(), 30)))


def _button(page: Page, words: str) -> Optional[Locator]:
    pattern = re.compile(rf"^(?:{words})$", re.IGNORECASE)
    buttons = page.locator('div[role="button"], button').filter(has_text=pattern)
    for i in range(buttons.count()):
        candidate = buttons.nth(i)
        if candidate.is_visible() and candidate.is_enabled():
            return candidate
    return None


def _login_prompt_visible(page: Page) -> bool:
    prompts = page.locator('a, button, div[role="button"]').filter(
        has_text=re.compile(r"^(?:Đăng nhập|Sign in)$", re.IGNORECASE)
    )
    return any(prompts.nth(i).is_visible() for i in range(prompts.count()))


def _form_ready(page: Page) -> bool:
    if (urlparse(page.url).hostname or "").lower() != "docs.google.com":
        return False
    if _login_prompt_visible(page):
        return False
    return page.locator('div[role="listitem"]').count() > 0 or _button(page, "Tiếp|Next|Gửi|Submit") is not None


def _unavailable(page: Page) -> bool:
    body = _normalize_text(page.locator("body").inner_text(timeout=1000)).casefold()
    return any(message in body for message in (
        "no longer accepting responses", "không còn chấp nhận câu trả lời",
        "không còn chấp nhận phản hồi", "bạn cần có quyền truy cập", "you need access",
    ))


def wait_for_form(page: Page, on_state: StateCallback) -> Optional[RunResult]:
    announced = False
    while True:
        if page.is_closed():
            return RunResult(RunStatus.CANCELLED, page.url if not page.is_closed() else "", "Trình duyệt đã đóng")
        if _form_ready(page):
            return None
        if _unavailable(page):
            return RunResult(RunStatus.FAILED, page.url, "Form đóng hoặc tài khoản không có quyền")
        if not announced:
            print("[LOGIN] Hãy đăng nhập tài khoản SV trong trình duyệt. Bot sẽ chờ đến khi form hiện ra.")
            on_state("awaiting_login", "Chờ đăng nhập")
            announced = True
        page.wait_for_timeout(500)


def _fingerprint(page: Page) -> str:
    items = page.locator('div[role="listitem"]')
    parts = []
    for i in range(items.count()):
        node = items.nth(i)
        parts.append((extract_question_label(node), node.get_attribute("data-params"),
                      [extract_option_text(node.locator('[role="radio"], [role="checkbox"]').nth(j))
                       for j in range(node.locator('[role="radio"], [role="checkbox"]').count())]))
    progress = page.locator('[role="progressbar"]').first
    return json.dumps([parts, progress.get_attribute("aria-valuenow") if progress.count() else None], ensure_ascii=False)


def _is_confirmation(page: Page) -> bool:
    if page.is_closed():
        return False
    text = _normalize_text(page.locator("body").inner_text(timeout=1000)).casefold()
    known_text = any(value in text for value in (
        "your response has been recorded", "your response has been submitted",
        "câu trả lời của bạn đã được ghi lại", "phản hồi của bạn đã được ghi lại",
    ))
    markers = page.locator('[class*="ConfirmationMessage"], [data-confirmation="true"]')
    confirmation = any(markers.nth(i).is_visible() for i in range(markers.count()))
    return (known_text or confirmation) and _button(page, "Gửi|Submit") is None


def _review(page: Page, questions: Sequence[dict[str, Any]], issues: dict[str, str],
            input_fn: InputCallback, on_state: StateCallback) -> bool:
    manual_ack: set[str] = set()
    while issues:
        on_state("awaiting_review", "; ".join(f"{key}: {reason}" for key, reason in issues.items()))
        for q in questions:
            if q["id"] in issues:
                print(f"[CẦN SỬA] {q['label']}: {issues[q['id']]}")
        for key, reason in issues.items():
            if key not in {q["id"] for q in questions}:
                print(f"[CẦN SỬA] {reason}")
        try:
            input_fn("Sửa trên trình duyệt rồi nhấn Enter để kiểm tra lại (Ctrl+C để hủy): ")
        except (EOFError, KeyboardInterrupt):
            return False
        manual_ack.update(q["id"] for q in questions if q["type"] == "unsupported")
        issues = inspect_questions(page, questions, manual_ack)
        issues.update(_page_issues(page))
    return True


def _wait_for_submit(page: Page, url: str) -> RunResult:
    disappeared_at: Optional[float] = None
    announced_error = False
    print("[CHỜ GỬI] Kiểm tra form rồi tự bấm Gửi. Bot sẽ chờ trang xác nhận.")
    while True:
        if page.is_closed():
            return RunResult(RunStatus.UNKNOWN, url, "Trang đóng khi đang chờ xác nhận gửi")
        if _is_confirmation(page):
            return RunResult(RunStatus.CONFIRMED, url, "Đã thấy trang xác nhận phản hồi")
        if _visible_error(page) and not announced_error:
            print("[CẦN SỬA] Form báo lỗi sau khi bấm Gửi; hãy sửa rồi bấm Gửi lại.")
            announced_error = True
        if not _form_ready(page):
            disappeared_at = disappeared_at or time.monotonic()
            if time.monotonic() - disappeared_at > CONFIRMATION_GRACE_SECONDS:
                return RunResult(RunStatus.UNKNOWN, url, "Rời form nhưng không thấy xác nhận gửi")
        else:
            disappeared_at = None
        page.wait_for_timeout(500)


def run_form(page: Page, url: str, on_state: StateCallback = lambda *_: None,
             input_fn: InputCallback = input) -> RunResult:
    target = validate_form_url(url)
    awaiting_submit = False
    try:
        on_state("running", "Đang mở form")
        page.goto(target, wait_until="domcontentloaded", timeout=60000)
        early = wait_for_form(page, on_state)
        if early:
            return RunResult(early.status, target, early.reason)
        for page_number in range(1, MAX_PAGES + 1):
            questions = parse_form_questions(page, page_number)
            on_state("running", f"Đang xử lý trang {page_number}")
            known_answers = profile_answers(questions)
            remaining = [q for q in questions if q["id"] not in known_answers and q["type"] != "unsupported"]
            try:
                raw_answers = ask_ai_in_batches(remaining) if remaining else {}
            except Exception as exc:
                reason = f"{AI_PROVIDER} không khả dụng sau {AI_RETRY_ATTEMPTS} lần thử: {_safe_ai_error(exc)}"
                print(f"[AI ERROR] {reason}")
                return RunResult(RunStatus.FAILED, target, reason)
            raw_answers.update(known_answers)
            accepted, issues = validate_answers(questions, raw_answers)
            merge_issues(issues, fill_form(page, questions, accepted))
            merge_issues(issues, inspect_questions(page, questions, set()))
            merge_issues(issues, _page_issues(page))
            if issues and not _review(page, questions, issues, input_fn, on_state):
                return RunResult(RunStatus.CANCELLED, target, "Bạn đã hủy khi rà soát")

            while True:
                next_button = _button(page, "Tiếp|Next")
                if next_button is None:
                    break
                before = _fingerprint(page)
                old_button = next_button.element_handle()
                next_button.click()
                deadline = time.monotonic() + 15
                changed = False
                while time.monotonic() < deadline:
                    if _visible_error(page):
                        break
                    if _fingerprint(page) != before:
                        changed = True
                        break
                    try:
                        changed = old_button is not None and not old_button.evaluate("el => el.isConnected")
                    except Exception:
                        changed = True
                    if changed:
                        break
                    page.wait_for_timeout(250)
                if changed:
                    break
                if _visible_error(page):
                    review_issues = inspect_questions(page, questions, set())
                    merge_issues(review_issues, _page_issues(page))
                    review_issues["form"] = "Form báo lỗi sau khi bấm Tiếp"
                    if not _review(page, questions, review_issues, input_fn, on_state):
                        return RunResult(RunStatus.CANCELLED, target, "Bạn đã hủy khi sửa lỗi")
                    continue
                return RunResult(RunStatus.FAILED, target, "Bấm Tiếp nhưng không thấy trang mới")
            if next_button is not None:
                early = wait_for_form(page, on_state)
                if early:
                    return RunResult(early.status, target, early.reason)
                continue

            if _button(page, "Gửi|Submit") is None:
                return RunResult(RunStatus.FAILED, target, "Không tìm thấy nút Gửi trên trang cuối")
            on_state("awaiting_submit", "Chờ bạn bấm Gửi và trang xác nhận")
            awaiting_submit = True
            return _wait_for_submit(page, target)
        return RunResult(RunStatus.FAILED, target, f"Vượt quá {MAX_PAGES} trang")
    except KeyboardInterrupt:
        return RunResult(RunStatus.UNKNOWN if awaiting_submit else RunStatus.CANCELLED, target, "Đã dừng bằng Ctrl+C")
    except Exception as exc:
        return RunResult(RunStatus.UNKNOWN if awaiting_submit else RunStatus.FAILED, target, str(exc))


def main(url: Optional[str] = None) -> RunResult:
    target = url or FORM_URL
    if not target:
        return RunResult(RunStatus.FAILED, "", "Thiếu FORM_URL")
    try:
        with sync_playwright() as playwright:
            context = launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                return run_form(page, target)
            finally:
                try:
                    context.close()
                except Exception:
                    # Cleanup must not replace a cancelled/unknown submission result.
                    pass
    except KeyboardInterrupt:
        return RunResult(RunStatus.CANCELLED, target, "Đã dừng bằng Ctrl+C")
    except Exception as exc:
        return RunResult(RunStatus.FAILED, target, str(exc))


if __name__ == "__main__":
    result = main()
    print(f"[{result.status.value.upper()}] {result.reason}")
    raise SystemExit(0 if result.confirmed else 1)
