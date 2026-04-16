import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import google.generativeai as genai
from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

from config import AI_CONFIG, USER_PROFILE


FORM_URL = os.getenv("FORM_URL", "")
USER_DATA_DIR = Path(os.getenv("USER_DATA_DIR", "./user_data"))
HEADLESS = os.getenv("HEADLESS", "false").strip().lower() == "true"


def _random_sleep(min_seconds: float = 0.4, max_seconds: float = 1.2) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _safe_key(label: str, index: int) -> str:
    normalized = _normalize_text(label)
    return normalized if normalized else f"question_{index + 1}"


def extract_question_label(question_node: Locator) -> str:
    selectors = [
        '[role="heading"]',
        '[aria-label][role="heading"]',
        'div[aria-label][role="textbox"]',
        '[data-params*="question"]',
    ]
    for selector in selectors:
        locator = question_node.locator(selector)
        if locator.count() == 0:
            continue
        try:
            candidate = _normalize_text(locator.first.inner_text(timeout=500))
            if candidate:
                return candidate
        except Exception:
            continue

    node_text = _normalize_text(question_node.inner_text(timeout=1000))
    return node_text.split("\n")[0] if node_text else ""


def extract_option_text(option_node: Locator) -> str:
    aria_label = _normalize_text(option_node.get_attribute("aria-label") or "")
    if aria_label:
        return aria_label

    visible_text = _normalize_text(option_node.inner_text(timeout=500))
    return visible_text


def detect_question_type(question_node: Locator) -> str:
    if question_node.locator('input[type="text"]').count() > 0 or question_node.locator("textarea").count() > 0:
        return "text"
    if question_node.locator('[role="radio"]').count() > 0:
        return "radio"
    if question_node.locator('[role="checkbox"]').count() > 0:
        return "checkbox"
    return "unknown"


def parse_form_questions(page: Page) -> List[Dict[str, Any]]:
    page.wait_for_selector('div[role="listitem"]', timeout=15000)
    questions: List[Dict[str, Any]] = []

    listitems = page.locator('div[role="listitem"]')
    for i in range(listitems.count()):
        node = listitems.nth(i)
        qtype = detect_question_type(node)
        if qtype == "unknown":
            continue

        label = extract_question_label(node)
        options: List[str] = []

        if qtype in {"radio", "checkbox"}:
            role_selector = '[role="radio"]' if qtype == "radio" else '[role="checkbox"]'
            role_nodes = node.locator(role_selector)
            for j in range(role_nodes.count()):
                opt_text = extract_option_text(role_nodes.nth(j))
                if opt_text:
                    options.append(opt_text)

        questions.append(
            {
                "index": i,
                "key": _safe_key(label, i),
                "label": label,
                "type": qtype,
                "options": options,
            }
        )

    return questions


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
    if fenced:
        text = fenced.group(1)

    json_match = re.search(r"(\{[\s\S]*\})", text)
    if json_match:
        text = json_match.group(1)

    return json.loads(text)


def build_ai_prompt(questions: Sequence[Dict[str, Any]]) -> str:
    """Builds a strict JSON-output prompt for Gemini using the static user profile and parsed form questions."""
    return (
        "Bạn là trợ lý điền Google Form. "
        "Dựa trên USER_PROFILE và danh sách câu hỏi, trả về DUY NHẤT JSON object theo dạng "
        '{"label_câu_hỏi": "câu_trả_lời"}. '
        "Với checkbox nhiều lựa chọn: trả về mảng string. "
        "Không thêm giải thích.\n\n"
        f"USER_PROFILE:\n{json.dumps(USER_PROFILE, ensure_ascii=False, indent=2)}\n\n"
        f"QUESTIONS:\n{json.dumps(list(questions), ensure_ascii=False, indent=2)}"
    )


def ask_gemini(questions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not AI_CONFIG.api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in .env")

    configure_kwargs: Dict[str, Any] = {"api_key": AI_CONFIG.api_key}
    if AI_CONFIG.api_base:
        configure_kwargs["client_options"] = {"api_endpoint": AI_CONFIG.api_base}
    genai.configure(**configure_kwargs)

    model = genai.GenerativeModel(AI_CONFIG.model)
    prompt = build_ai_prompt(questions)
    response = model.generate_content(prompt)
    output_text = (response.text or "").strip()
    if not output_text:
        raise RuntimeError("Gemini returned empty content")

    return _extract_json(output_text)


def _find_matching_option(question_node: Locator, role_selector: str, answer: str) -> Optional[Locator]:
    target = _normalize_text(answer).casefold()
    option_nodes = question_node.locator(role_selector)

    for i in range(option_nodes.count()):
        opt = option_nodes.nth(i)
        text = _normalize_text(extract_option_text(opt)).casefold()
        if text == target:
            return opt

    for i in range(option_nodes.count()):
        opt = option_nodes.nth(i)
        text = _normalize_text(extract_option_text(opt)).casefold()
        if target in text or text in target:
            return opt

    return None


def _type_like_human(field: Locator, text: str) -> None:
    field.click()
    field.fill("")
    for ch in text:
        field.type(ch, delay=random.randint(35, 120))


def fill_form(page: Page, questions: Sequence[Dict[str, Any]], answers: Dict[str, Any]) -> None:
    listitems = page.locator('div[role="listitem"]')

    for q in questions:
        answer = answers.get(q["label"])
        if answer is None:
            answer = answers.get(q["key"])
        if answer is None:
            continue

        node = listitems.nth(q["index"])
        qtype = q["type"]

        if qtype == "text":
            field = node.locator("textarea, input[type='text']").first
            _type_like_human(field, str(answer))

        elif qtype == "radio":
            target = _find_matching_option(node, '[role="radio"]', str(answer))
            if target:
                target.click()

        elif qtype == "checkbox":
            selected_values: List[str]
            if isinstance(answer, list):
                selected_values = [str(item) for item in answer]
            elif isinstance(answer, str):
                selected_values = [v.strip() for v in re.split(r"[,;\n]", answer) if v.strip()]
            else:
                selected_values = [str(answer)]

            for value in selected_values:
                target = _find_matching_option(node, '[role="checkbox"]', value)
                if target:
                    target.click()
                    _random_sleep(0.2, 0.7)

        _random_sleep(0.6, 1.8)


def main(url: Optional[str] = None) -> bool:
    target_url = url or FORM_URL
    if not target_url:
        print("[ERROR] Missing FORM_URL in .env and no URL provided")
        return False

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            _random_sleep(1.0, 2.0)

            # Check if we are at Google Login page
            if "accounts.google.com" in page.url or page.locator('input[type="email"]').count() > 0:
                print("[!] Phát hiện trang đăng nhập Google. Vui lòng thực hiện đăng nhập trong trình duyệt...")
                print("[!] Bot sẽ tự động tiếp tục sau khi bạn vào được trang Form.")
                try:
                    # Wait longer for the form to appear after login (up to 5 minutes)
                    page.wait_for_selector('div[role="listitem"]', timeout=300000)
                    print("[INFO] Đã đăng nhập thành công hoặc đã vào được Form.")
                except Exception:
                    print("[ERROR] Quá thời gian chờ đăng nhập. Vui lòng chạy lại script.")
                    return False

            page_count = 1
            while True:
                print(f"--- Đang xử lý trang {page_count} ---")
                questions = parse_form_questions(page)
                if questions:
                    print("[INFO] Parsed questions:", json.dumps(questions, ensure_ascii=False, indent=2))
                    answers = ask_gemini(questions)
                    print("[INFO] AI answers:", json.dumps(answers, ensure_ascii=False, indent=2))
                    fill_form(page, questions, answers)
                    _random_sleep(1.0, 2.0)
                
                # Tìm nút "Tiếp" hoặc "Next"
                next_btn = page.locator('div[role="button"]').filter(has_text=re.compile(r"^(Tiếp|Next)$", re.IGNORECASE))
                if next_btn.count() > 0:
                    print(f"[INFO] Bấm nút Tiếp tục...")
                    next_btn.first.click()
                    page.wait_for_timeout(3000)
                    page_count += 1
                else:
                    print("[INFO] Đã đến trang cuối cùng của Form.")
                    submit_btn = page.locator('div[role="button"]').filter(has_text=re.compile(r"Gửi|Submit", re.IGNORECASE))
                    if submit_btn.count() == 0:
                        # Fallback: match by aria-label
                        submit_btn = page.locator(
                            'div[role="button"][aria-label*="Gửi"], div[role="button"][aria-label*="Submit"]'
                        )
                    if submit_btn.count() > 0:
                        print("[INFO] Đang tiến hành Auto Submit...")
                        submit_btn.first.click()
                        page.wait_for_timeout(4000)
                        print("[INFO] Đã Submit Form thành công!")
                    break

            screenshot_path = Path(f"form_filled_preview_{page_count}.png")
            page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"[INFO] Preview screenshot saved: {screenshot_path.resolve()}")
            print("[INFO] Hoàn tất điền form.")
            return True
        except Exception as e:
            print(f"[ERROR] Quá trình điền form thất bại: {e}")
            return False
        finally:
            context.close()


if __name__ == "__main__":
    main()
