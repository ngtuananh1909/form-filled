from types import SimpleNamespace

import pytest

import test_gemini as probe


def test_probe_health_question_explicitly_requests_ok():
    assert probe._health_check_questions()[0]["label"] == (
        "Chọn chính xác đáp án OK để kiểm tra kết nối"
    )


@pytest.mark.parametrize(
    ("provider", "model", "expected_route"),
    [
        ("openrouter", "fake-openrouter-model", "openrouter"),
        ("openai_compatible", "fake-local-model", "openai_compatible"),
        ("gemini", "fake-gemini-model", "gemini"),
    ],
)
def test_probe_routes_selected_provider_and_reports_success(
    monkeypatch, capsys, provider, model, expected_route
):
    routes = []

    def fake_openrouter(questions):
        routes.append(("openrouter", questions))
        return {"health": "o0"}

    def fake_gemini(questions):
        routes.append(("gemini", questions))
        return {"health": "o0"}

    def fake_local(questions):
        routes.append(("openai_compatible", questions))
        return {"health": "o0"}

    monkeypatch.setattr(probe.form_main, "AI_PROVIDER", provider)
    monkeypatch.setattr(probe.form_main, "OPENROUTER_MODEL", model)
    monkeypatch.setattr(probe.form_main, "OPENAI_COMPATIBLE_MODEL", model, raising=False)
    monkeypatch.setattr(probe.form_main, "AI_CONFIG", SimpleNamespace(model=model))
    monkeypatch.setattr(probe.form_main, "ask_openrouter", fake_openrouter)
    monkeypatch.setattr(probe.form_main, "ask_gemini", fake_gemini)
    monkeypatch.setattr(probe.form_main, "ask_openai_compatible", fake_local, raising=False)

    assert probe.main() == 0

    assert [route for route, _questions in routes] == [expected_route]
    assert routes[0][1][0]["id"] == "health"
    assert routes[0][1][0]["choices"] == [
        {"id": "o0", "label": "OK"},
        {"id": "o1", "label": "FAILED"},
    ]
    assert capsys.readouterr().out == f"[OK] {provider}: {model}\n"


def test_probe_reports_provider_error_and_returns_one(monkeypatch, capsys):
    monkeypatch.setattr(probe.form_main, "AI_PROVIDER", "gemini")

    def fail(_questions):
        raise RuntimeError("offline provider failure")

    monkeypatch.setattr(probe.form_main, "ask_ai", fail)

    assert probe.main() == 1
    assert capsys.readouterr().out == "[ERROR] gemini: offline provider failure\n"


def test_probe_reports_invalid_answer_and_returns_one(monkeypatch, capsys):
    monkeypatch.setattr(probe.form_main, "AI_PROVIDER", "openrouter")
    monkeypatch.setattr(probe.form_main, "ask_ai", lambda _questions: {"health": "not-a-choice"})

    assert probe.main() == 1
    output = capsys.readouterr().out
    assert output.startswith("[ERROR] openrouter: Phản hồi không hợp lệ:")
    assert "health" in output


def test_probe_uses_synthetic_profile_and_restores_profile_on_success(monkeypatch, capsys):
    original_profile = {"private_marker": "must-not-be-sent"}
    monkeypatch.setattr(probe.form_main, "USER_PROFILE", original_profile)
    prompts = []

    def fake_ask(questions):
        prompts.append(probe.form_main.build_ai_prompt(questions))
        return {"health": "o0"}

    monkeypatch.setattr(probe.form_main, "ask_ai", fake_ask)

    assert probe.main() == 0
    assert "must-not-be-sent" not in prompts[0]
    assert "USER_PROFILE:\n{}" in prompts[0]
    assert probe.form_main.USER_PROFILE is original_profile
    assert capsys.readouterr().out.startswith("[OK] ")


def test_probe_restores_profile_when_provider_fails(monkeypatch, capsys):
    original_profile = {"private_marker": "must-not-be-sent"}
    monkeypatch.setattr(probe.form_main, "USER_PROFILE", original_profile)
    prompts = []

    def fail(questions):
        prompts.append(probe.form_main.build_ai_prompt(questions))
        raise RuntimeError("offline provider failure")

    monkeypatch.setattr(probe.form_main, "ask_ai", fail)

    assert probe.main() == 1
    assert "must-not-be-sent" not in prompts[0]
    assert "USER_PROFILE:\n{}" in prompts[0]
    assert probe.form_main.USER_PROFILE is original_profile
    assert capsys.readouterr().out.endswith("offline provider failure\n")
