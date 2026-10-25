"""Optional manual check for the configured AI provider."""

import main as form_main


def _health_check_questions() -> list[dict[str, object]]:
    return [{
        "id": "health", "label": "Chọn chính xác đáp án OK để kiểm tra kết nối", "type": "radio",
        "options": ["OK", "FAILED"],
        "choices": [{"id": "o0", "label": "OK"}, {"id": "o1", "label": "FAILED"}],
    }]


def _provider_model() -> str:
    if form_main.AI_PROVIDER == "openrouter":
        return form_main.OPENROUTER_MODEL
    if form_main.AI_PROVIDER == "openai_compatible":
        return form_main.OPENAI_COMPATIBLE_MODEL
    return getattr(form_main.AI_CONFIG, "model", "").strip() or "gemini-flash-latest"


def main() -> int:
    original_profile = form_main.USER_PROFILE
    try:
        form_main.USER_PROFILE = {}
        questions = _health_check_questions()
        answers = form_main.ask_ai(questions)
        accepted, issues = form_main.validate_answers(questions, answers)
        if issues or not accepted:
            raise RuntimeError(f"Phản hồi không hợp lệ: {issues}")
        print(f"[OK] {form_main.AI_PROVIDER}: {_provider_model()}")
        return 0
    except Exception as exc:
        print(f"[ERROR] {form_main.AI_PROVIDER}: {exc}")
        return 1
    finally:
        form_main.USER_PROFILE = original_profile


if __name__ == "__main__":
    raise SystemExit(main())
