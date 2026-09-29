from pathlib import Path
import json
from io import BytesIO
from urllib.error import HTTPError
import fcntl
import os

import pytest

import bulk_runner
import main


FORM_A = "https://docs.google.com/forms/d/e/AAA/viewform"
FORM_B = "https://docs.google.com/forms/d/e/BBB/viewform"


class FakeNode:
    @property
    def first(self):
        return self

    def locator(self, _selector):
        return EmptyNode()

    def inner_text(self, **_kwargs):
        return "Câu hỏi nhiều dòng?\nLựa chọn A\nLựa chọn B"


class EmptyNode(FakeNode):
    def count(self):
        return 0


def test_label_fallback_keeps_only_first_line():
    assert main.extract_question_label(FakeNode()) == "Câu hỏi nhiều dòng?"


def test_question_ids_allow_duplicate_labels_and_reject_ambiguous_answers():
    questions = [
        {"id": "p1.q0", "label": "Chọn", "type": "radio", "options": ["A", "B"]},
        {"id": "p1.q1", "label": "Chọn", "type": "checkbox", "options": ["A", "B"]},
        {"id": "p1.q2", "label": "Email", "type": "text", "options": []},
        {"id": "p1.q3", "label": "Ngày", "type": "unsupported", "options": []},
    ]
    accepted, issues = main.validate_answers(questions, {
        "p1.q0": "a", "p1.q1": ["A", "A"], "p1.q2": "Không có thông tin", "p1.q3": "ngày mai",
    })
    assert accepted == {"p1.q0": "A"}
    assert set(issues) == {"p1.q1", "p1.q2", "p1.q3"}


def test_invalid_or_missing_ai_values_never_become_random_answers():
    questions = [{"id": "p1.q0", "label": "Chọn", "type": "radio", "options": ["A", "B"]}]
    assert main.validate_answers(questions, {}) == ({}, {"p1.q0": "AI không trả choice id/lựa chọn hợp lệ cho radio"})
    assert main.validate_answers(questions, {"p1.q0": "C"})[0] == {}
    assert main.validate_answers(questions, {"p1.q0": ""})[0] == {}


def test_choice_ids_are_preferred_but_exact_labels_remain_compatible(monkeypatch):
    monkeypatch.setattr(main, "USER_PROFILE", {"ho_ten": "Test User"})
    question = {
        "id": "p1.q0", "label": "Câu hỏi", "type": "radio", "options": ["Đáp án A", "Đáp án B"],
        "choices": [{"id": "o0", "label": "Đáp án A"}, {"id": "o1", "label": "Đáp án B"}],
    }
    assert main.validate_answers([question], {"p1.q0": "o1"})[0] == {"p1.q0": "Đáp án B"}
    assert main.validate_answers([question], {"p1.q0": "Đáp án A"})[0] == {"p1.q0": "Đáp án A"}
    prompt = main.build_ai_prompt([question])
    assert '"id": "o0"' in prompt and 'không phải nhãn' in prompt


def test_ai_prompt_does_not_disclose_profile_for_choice_questions(monkeypatch):
    monkeypatch.setattr(main, "USER_PROFILE", {"private_marker": "personal-secret-marker"})
    question = {"id": "p1.q0", "label": "Chọn A", "type": "radio", "options": ["A", "B"]}
    assert "personal-secret-marker" not in main.build_ai_prompt([question])


@pytest.mark.parametrize("url", [
    "https://someone:password@docs.google.com/forms/d/e/AAA/viewform",
    "https://forms.gle/",
    "https://docs.google.com/forms/",
])
def test_form_url_rejects_credentials_and_missing_form_id(url):
    with pytest.raises(ValueError):
        main.validate_form_url(url)


def test_openrouter_http_error_does_not_echo_provider_body(monkeypatch):
    monkeypatch.setattr(main, "OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setattr(main, "AI_RETRY_ATTEMPTS", 1)

    def fail(_request, timeout):
        raise HTTPError("https://openrouter.ai", 401, "Unauthorized", {}, BytesIO(b"private-prompt-marker"))

    monkeypatch.setattr(main, "urlopen", fail)
    with pytest.raises(RuntimeError) as error:
        main.ask_openrouter([{"id": "q", "label": "Question", "type": "radio", "options": ["A"]}])
    assert "HTTP 401" in str(error.value)
    assert "private-prompt-marker" not in str(error.value)


@pytest.mark.parametrize("base_url, expected_url", [
    ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1/chat/completions"),
    ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
    ("http://localhost:8000/v1/chat/completions", "http://localhost:8000/v1/chat/completions"),
])
def test_local_openai_compatible_provider_uses_configured_server_model_and_key(
    monkeypatch, base_url, expected_url
):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"p1.q0":"o1"}'}}]}).encode()

    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(main, "urlopen", fake_urlopen)
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_BASE_URL", base_url, raising=False)
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_API_KEY", "local-test-key", raising=False)
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_MODEL", "my-local-model", raising=False)
    question = {"id": "p1.q0", "label": "Choose", "type": "radio", "options": ["A", "B"]}

    assert main.ask_openai_compatible([question]) == {"p1.q0": "o1"}
    assert len(requests) == 1
    assert requests[0].full_url == expected_url
    assert requests[0].get_header("Authorization") == "Bearer local-test-key"
    assert json.loads(requests[0].data)["model"] == "my-local-model"


def test_local_provider_accepts_server_without_api_key(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"q\\":\\"o0\\"}"}}]}'

    requests = []
    monkeypatch.setattr(main, "urlopen", lambda request, timeout: (requests.append(request), FakeResponse())[1])
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_BASE_URL", "http://127.0.0.1:1234/v1", raising=False)
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_API_KEY", "", raising=False)
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_MODEL", "local-model", raising=False)
    assert main.ask_openai_compatible([{"id": "q", "label": "Q", "type": "radio", "options": ["A"]}]) == {"q": "o0"}
    assert requests[0].get_header("Authorization") is None


@pytest.mark.parametrize("base_url", [
    "", "https://user:pass@localhost:1234/v1", "file:///tmp/model", "http://localhost:1234/v1?token=secret",
])
def test_local_provider_rejects_unsafe_or_missing_base_url(monkeypatch, base_url):
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_BASE_URL", base_url, raising=False)
    monkeypatch.setattr(main, "OPENAI_COMPATIBLE_MODEL", "local-model", raising=False)
    with pytest.raises(ValueError):
        main.ask_openai_compatible([{"id": "q", "label": "Q", "type": "text"}])


def test_ai_provider_routes_to_local_compatible_server(monkeypatch):
    monkeypatch.setattr(main, "AI_PROVIDER", "openai_compatible")
    monkeypatch.setattr(main, "ask_openai_compatible", lambda _questions: {"q": "o0"}, raising=False)
    assert main.ask_ai([{"id": "q"}]) == {"q": "o0"}


def test_legacy_unique_question_labels_are_normalized_to_question_ids():
    questions = [
        {"id": "p1.q0", "label": "Câu A", "type": "radio", "options": ["A", "B"]},
        {"id": "p1.q1", "label": "Câu B", "type": "radio", "options": ["A", "B"]},
    ]
    normalized, ignored = main.normalize_answer_keys(questions, {"Câu A": "A", "không tồn tại": "B"})
    assert normalized == {"p1.q0": "A"}
    assert ignored == ["không tồn tại"]


def test_validation_reason_is_not_overwritten_by_empty_dom_state():
    original = {"p1.q0": "AI không trả choice id/lựa chọn hợp lệ cho radio"}
    assert main.merge_issues(original, {"p1.q0": "Chưa chọn đáp án"}) == {
        "p1.q0": "AI không trả choice id/lựa chọn hợp lệ cho radio"
    }


def test_profile_fields_are_used_directly_and_placeholders_are_rejected(monkeypatch):
    monkeypatch.setattr(main, "USER_PROFILE", {"ho_ten": "Nguyen Van A", "mssv": "SV001", "lop": "Your Class"})
    questions = [
        {"id": "p1.q0", "label": "HỌ VÀ TÊN *", "type": "text"},
        {"id": "p1.q1", "label": "MSSV *", "type": "text"},
        {"id": "p1.q2", "label": "LỚP *", "type": "text"},
        {"id": "p1.q3", "label": "Bạn thích lớp nào?", "type": "text"},
    ]
    assert main.profile_answers(questions) == {"p1.q0": "Nguyen Van A", "p1.q1": "SV001"}


def test_transient_ai_error_is_retried_without_random_fallback(monkeypatch):
    class FakeModel:
        calls = 0

        def generate_content(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise main.google_errors.ServiceUnavailable("busy")
            return object()

    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    model = FakeModel()
    assert main._generate_with_retry(model, "prompt") is not None
    assert model.calls == 3


def test_openrouter_uses_requested_free_model_and_extracts_json(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "```json\n{\"p1.q0\": \"o1\"}\n```"}}]}).encode()

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(main, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(main, "OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    monkeypatch.setattr(main, "urlopen", fake_urlopen)
    questions = [{"id": "p1.q0", "label": "Câu hỏi", "type": "radio", "options": ["A", "B"]}]
    assert main.ask_openrouter(questions) == {"p1.q0": "o1"}
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["body"]["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_batching_keeps_all_ai_answers(monkeypatch):
    questions = [
        {"id": f"p1.q{i}", "label": f"Q{i}", "type": "radio", "options": ["A", "B"]}
        for i in range(5)
    ]
    calls = []

    def fake_ask(batch):
        calls.append([q["id"] for q in batch])
        return {q["id"]: "o0" for q in batch}

    monkeypatch.setattr(main, "AI_BATCH_SIZE", 2)
    monkeypatch.setattr(main, "ask_ai", fake_ask)
    assert main.ask_ai_in_batches(questions) == {q["id"]: "o0" for q in questions}
    assert calls == [["p1.q0", "p1.q1"], ["p1.q2", "p1.q3"], ["p1.q4"]]


def test_load_links_deduplicates_and_validates(tmp_path: Path):
    path = tmp_path / "links.txt"
    path.write_text(f"# header\n{FORM_A}\n{FORM_A}\n{FORM_B}\n", encoding="utf-8")
    assert bulk_runner.load_links(path) == [FORM_A, FORM_B]
    path.write_text("https://example.com/form\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bulk_runner.load_links(path)


def test_short_and_full_urls_for_one_form_run_only_once(tmp_path: Path, monkeypatch):
    short = "https://forms.gle/shortAlias"
    links = tmp_path / "links.txt"
    links.write_text(f"{short}\n{FORM_A}\n", encoding="utf-8")
    monkeypatch.setattr(bulk_runner, "LEGACY_FILE", tmp_path / "missing.txt")
    monkeypatch.setattr(main, "HEADLESS", False)

    class FakePage:
        url = "about:blank"

        def goto(self, _url, **_kwargs):
            self.url = FORM_A

    class FakeContext:
        pages = [FakePage()]

        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    calls = []
    monkeypatch.setattr(bulk_runner, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(main, "launch_context", lambda _playwright: FakeContext())

    def fake_run(_page, url, on_state):
        calls.append(url)
        return main.RunResult(main.RunStatus.CONFIRMED, url)

    monkeypatch.setattr(main, "run_form", fake_run)
    results = bulk_runner.run(links_file=links, ledger_path=tmp_path / "runs.sqlite3")
    assert len(results) == 1
    assert calls == [short]


def test_second_run_cannot_start_while_ledger_has_an_active_run(tmp_path: Path, monkeypatch):
    links = tmp_path / "links.txt"
    links.write_text(FORM_A + "\n", encoding="utf-8")
    ledger_path = tmp_path / "runs.sqlite3"
    lock_fd = os.open(str(ledger_path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(bulk_runner, "LEGACY_FILE", tmp_path / "missing.txt")
    monkeypatch.setattr(main, "HEADLESS", False)
    monkeypatch.setattr(bulk_runner, "sync_playwright", lambda: pytest.fail("second browser started"))
    try:
        with pytest.raises(RuntimeError, match="đang chạy"):
            bulk_runner.run(links_file=links, ledger_path=ledger_path)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_new_ledger_is_private_to_owner(tmp_path: Path):
    path = tmp_path / "runs.sqlite3"
    with bulk_runner.RunLedger(path):
        pass
    assert path.stat().st_mode & 0o077 == 0


def test_legacy_import_and_crash_recovery(tmp_path: Path):
    legacy = tmp_path / "processed_links.txt"
    legacy.write_text(f"{FORM_A}\n", encoding="utf-8")
    with bulk_runner.RunLedger(tmp_path / "runs.sqlite3") as ledger:
        ledger.migrate_legacy(legacy)
        assert ledger.state(FORM_A) == "legacy_unverified"
        ledger.record(FORM_B, "awaiting_submit")
        ledger.recover_interrupted()
        assert ledger.state(FORM_B) == "unknown"
        ledger.migrate_legacy(legacy)
        assert ledger.state(FORM_A) == "legacy_unverified"


def test_batch_stops_on_unknown_and_never_retries_it(tmp_path: Path, monkeypatch):
    links = tmp_path / "links.txt"
    links.write_text(f"{FORM_A}\n{FORM_B}\n", encoding="utf-8")
    monkeypatch.setattr(bulk_runner, "LEGACY_FILE", tmp_path / "missing.txt")
    monkeypatch.setattr(main, "HEADLESS", False)

    class FakeContext:
        pages = [object()]

        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(bulk_runner, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(main, "launch_context", lambda _playwright: FakeContext())
    calls = []

    def fake_run(_page, url, on_state):
        calls.append(url)
        on_state("awaiting_submit", "Chờ Gửi")
        return main.RunResult(main.RunStatus.UNKNOWN, url, "Không rõ đã gửi")

    monkeypatch.setattr(main, "run_form", fake_run)
    database = tmp_path / "runs.sqlite3"
    result = bulk_runner.run(links_file=links, ledger_path=database)
    assert len(result) == 1 and result[0].status == main.RunStatus.UNKNOWN
    assert calls == [FORM_A]
    second = bulk_runner.run(links_file=links, ledger_path=database)
    assert len(second) == 1 and second[0].url == FORM_B
    assert calls == [FORM_A, FORM_B]
    assert calls.count(FORM_A) == 1
    with bulk_runner.RunLedger(database) as ledger:
        assert ledger.state(FORM_A) == "unknown"


def test_accuracy_rejects_repeat_and_spam():
    with pytest.raises(ValueError):
        bulk_runner.run(repeat=2)
    with pytest.raises(ValueError):
        bulk_runner.run(mode="spam")


def test_browser_cleanup_failure_does_not_mask_cancelled_result(monkeypatch):
    class FakeContext:
        pages = [object()]

        def close(self):
            raise RuntimeError("driver already closed")

    class FakePlaywright:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(main, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(main, "launch_context", lambda _playwright: FakeContext())
    monkeypatch.setattr(main, "run_form", lambda _page, url: main.RunResult(main.RunStatus.CANCELLED, url))
    assert main.main(FORM_A).status == main.RunStatus.CANCELLED
