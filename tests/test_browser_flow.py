"""Local browser fixtures: no Google account, API key, or real submission."""

import os

import pytest
from playwright.sync_api import sync_playwright

import main


TEST_URL = "https://docs.google.com/forms/d/e/local-test/viewform"


@pytest.fixture
def browser_page():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:
            if os.getenv("REQUIRE_BROWSER_TESTS") == "1":
                raise
            pytest.skip(f"Chromium không mở được trong sandbox: {exc}")
        try:
            page = browser.new_page()
            yield page
        finally:
            browser.close()


def test_parser_ids_grid_other_and_unsupported(browser_page):
    browser_page.set_content("""
      <div role="listitem"><div role="heading">Cùng nhãn</div>
        <div role="radio" aria-label="A"></div><input type="text" placeholder="Khác"></div>
      <div role="listitem"><div role="heading">Cùng nhãn</div><textarea></textarea></div>
      <div role="listitem"><div role="heading">Lưới</div>
        <div role="row"><div role="rowheader">Hàng một</div>
          <div role="radio" aria-label="Có" aria-checked="false"
               onclick="this.setAttribute('aria-checked','true')">Có</div>
          <div role="radio" aria-label="Không" aria-checked="false">Không</div></div></div>
      <div role="listitem"><div role="heading">Ngày</div><input type="date"></div>
    """)
    questions = main.parse_form_questions(browser_page, 1)
    assert [q["id"] for q in questions] == ["p1.q0", "p1.q1", "p1.q2.r0", "p1.q3"]
    assert [q["type"] for q in questions] == ["radio", "text", "radio", "unsupported"]
    assert questions[2]["label"] == "Lưới — Hàng một"
    assert main.fill_form(browser_page, [questions[2]], {"p1.q2.r0": "Có"}) == {}


def test_checkbox_fill_removes_stale_selection(browser_page):
    browser_page.set_content("""
      <div role="listitem"><div role="heading">Chọn mục</div>
        <div role="checkbox" aria-label="A" aria-checked="false"
             onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') === 'true' ? 'false' : 'true')">A</div>
        <div role="checkbox" aria-label="B" aria-checked="true"
             onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') === 'true' ? 'false' : 'true')">B</div>
      </div>
    """)
    questions = main.parse_form_questions(browser_page, 1)
    assert main.fill_form(browser_page, questions, {"p1.q0": ["A"]}) == {}
    checked = browser_page.locator('[role="checkbox"][aria-checked="true"]')
    assert checked.count() == 1 and checked.first.get_attribute("aria-label") == "A"


def test_dropdown_ignores_placeholder_and_selects_choice_id(browser_page):
    browser_page.set_content("""
      <div role="listitem"><div role="heading">Khoa</div><div role="listbox">
        <div role="option" aria-selected="true">Chọn</div>
        <div role="option" aria-selected="false" onclick="this.parentElement.querySelectorAll('[role=option]').forEach(x => x.setAttribute('aria-selected','false')); this.setAttribute('aria-selected','true')">CNTT</div>
        <div role="option" aria-selected="false">Dược</div>
      </div></div>
    """)
    question = main.parse_form_questions(browser_page, 1)[0]
    assert question["type"] == "dropdown"
    assert question["options"] == ["CNTT", "Dược"]
    assert main.fill_form(browser_page, [question], {"p1.q0": "CNTT"}) == {}
    assert main.inspect_questions(browser_page, [question], set()) == {}


def test_visible_form_still_waits_if_sign_in_prompt_is_present(browser_page):
    html = '<div role="listitem"><div role="heading">Question</div><input type="text"></div><button>Sign in</button>'
    browser_page.route(TEST_URL, lambda route: route.fulfill(body=html, content_type="text/html"))
    browser_page.goto(TEST_URL)
    assert main._form_ready(browser_page) is False
    browser_page.locator("button").evaluate("el => el.remove()")
    assert main._form_ready(browser_page) is True


def test_email_collection_consent_is_manual_and_outside_questions(browser_page):
    browser_page.set_content("""
      <div data-user-email-address="student@example.edu">
        <div role="checkbox" aria-checked="false"
             onclick="this.setAttribute('aria-checked','true')">Record my email</div>
      </div>
      <div role="listitem"><div role="heading">Name</div><input type="text"></div>
    """)
    assert "email_consent" in main._page_issues(browser_page)
    browser_page.locator('[data-user-email-address] [role="checkbox"]').click()
    assert main._page_issues(browser_page) == {}


@pytest.mark.parametrize("message", [
    "Biểu mẫu này không còn chấp nhận phản hồi.",
    "Bạn cần có quyền truy cập",
])
def test_closed_or_inaccessible_form_stops_instead_of_waiting_for_login(browser_page, message):
    browser_page.set_content(f"<main>{message}</main>")
    assert main._unavailable(browser_page) is True
    result = main.wait_for_form(browser_page, lambda *_: None)
    assert result.status == main.RunStatus.FAILED


def test_hidden_confirmation_marker_cannot_confirm_submission(browser_page):
    browser_page.set_content('<div class="ConfirmationMessage" hidden>Recorded</div>')
    assert main._is_confirmation(browser_page) is False


def test_provider_error_cannot_persist_private_response_text(browser_page, monkeypatch, capsys):
    html = '<div role="listitem"><div role="heading">Question</div><input type="text"></div>'
    browser_page.route(TEST_URL, lambda route: route.fulfill(body=html, content_type="text/html"))
    monkeypatch.setattr(main, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(main, "AI_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(main, "ask_gemini", lambda _questions: (_ for _ in ()).throw(
        RuntimeError("private-prompt-marker")
    ))
    result = main.run_form(browser_page, TEST_URL)
    assert result.status == main.RunStatus.FAILED
    assert "private-prompt-marker" not in result.reason
    assert "private-prompt-marker" not in capsys.readouterr().out


FORM_HTML = """
<!doctype html><meta charset="utf-8"><script>
window.pageName = 'login';
window.submitCount = 0;
function renderFirst() {
  window.pageName = 'first';
  document.body.innerHTML = `<div role="listitem"><div role="heading">Môn học?</div>
    <div role="radio" aria-label="A" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">A</div>
    <div role="radio" aria-label="B" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">B</div>
    </div><button onclick="renderSecond()">Next</button>`;
}
function renderSecond() {
  window.pageName = 'second';
  document.body.innerHTML = `<div role="listitem"><div role="heading">Mã SV?</div>
    <input type="text"></div><button id="submit" onclick="submitForm()">Submit</button>`;
}
function submitForm() {
  window.submitCount++;
  window.submittedAt = Date.now();
  document.body.innerHTML = '<div data-confirmation="true">Your response has been recorded.</div>';
}
document.addEventListener('DOMContentLoaded', () => {
  document.body.innerHTML = '<h1>Student login</h1><button>Sign in</button>';
  setTimeout(renderFirst, 350);
});
</script>
"""


def test_login_same_count_pages_manual_fix_and_manual_submit(browser_page, monkeypatch):
    browser_page.route(TEST_URL, lambda route: route.fulfill(body=FORM_HTML, content_type="text/html"))
    monkeypatch.setattr(main, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(main, "ask_gemini", lambda questions: {
        "p1.q0": "A" if questions[0]["id"] == "p1.q0" else None,
        "p2.q0": None,
    })
    states = []

    def manual_fix(_prompt):
        assert browser_page.evaluate("window.pageName") == "second"
        browser_page.locator("input[type=text]").fill("SV001")
        browser_page.evaluate("""() => {
            window.scheduledAt = Date.now();
            setTimeout(() => document.querySelector('#submit').click(), 700);
        }""")
        return ""

    result = main.run_form(browser_page, TEST_URL,
                           on_state=lambda state, _reason: states.append(state), input_fn=manual_fix)
    assert result.status == main.RunStatus.CONFIRMED
    assert "awaiting_login" in states and "awaiting_review" in states and "awaiting_submit" in states
    assert browser_page.evaluate("window.submitCount") == 1
    assert browser_page.evaluate("window.submittedAt - window.scheduledAt") >= 600


def test_unknown_confirmation_never_counts_as_success(browser_page, monkeypatch):
    html = """<div role="listitem"><div role="heading">Question</div><input type="text"></div>
      <button id="submit" onclick="document.body.innerHTML='<div>Done with no confirmation</div>'">Submit</button>"""
    browser_page.route(TEST_URL, lambda route: route.fulfill(body=html, content_type="text/html"))
    monkeypatch.setattr(main, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(main, "ask_gemini", lambda _questions: {"p1.q0": "answer"})
    monkeypatch.setattr(main, "CONFIRMATION_GRACE_SECONDS", 0.3)

    def schedule_click(state, _reason):
        if state == "awaiting_submit":
            browser_page.evaluate("setTimeout(() => document.querySelector('#submit').click(), 200)")

    result = main.run_form(browser_page, TEST_URL, on_state=schedule_click)
    assert result.status == main.RunStatus.UNKNOWN


def test_short_forms_url_redirects_to_ready_form_without_auto_submit(browser_page, monkeypatch):
    short_url = "https://forms.gle/local-test"
    html = """<div role="listitem"><div role="heading">Question</div>
      <input type="text"></div><button id="submit">Submit</button>"""
    # Playwright does not intercept the target of a fulfilled HTTP redirect;
    # a local page navigation exercises the same short-link readiness path.
    browser_page.route(short_url, lambda route: route.fulfill(
        body=f"<script>location.replace({TEST_URL!r})</script>", content_type="text/html"
    ))
    browser_page.route(TEST_URL, lambda route: route.fulfill(body=html, content_type="text/html"))
    monkeypatch.setattr(main, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(main, "ask_gemini", lambda _questions: {"p1.q0": "synthetic answer"})
    observed = {}

    def stop_before_submit(state, _reason):
        if state == "awaiting_submit":
            observed["url"] = browser_page.url
            observed["value"] = browser_page.locator("input[type=text]").input_value()
            observed["button_count"] = browser_page.locator("#submit").count()
            browser_page.close()

    result = main.run_form(browser_page, short_url, on_state=stop_before_submit)
    assert result.status == main.RunStatus.UNKNOWN
    assert observed == {"url": TEST_URL, "value": "synthetic answer", "button_count": 1}


def test_identical_questions_on_next_page_are_not_treated_as_stuck(browser_page, monkeypatch):
    html = """<script>
      function render(last) {
        document.body.innerHTML = `<div role="listitem"><div role="heading">Same question</div>
          <div role="radio" aria-label="A" aria-checked="false"
            onclick="this.setAttribute('aria-checked','true')">A</div></div>`
          + (last ? '<button>Submit</button>' : '<button onclick="render(true)">Next</button>');
      }
      document.addEventListener('DOMContentLoaded', () => render(false));
    </script>"""
    browser_page.route(TEST_URL, lambda route: route.fulfill(body=html, content_type="text/html"))
    monkeypatch.setattr(main, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(main, "ask_gemini", lambda questions: {questions[0]["id"]: "o0"})
    states = []

    def stop_before_submit(state, _reason):
        states.append(state)
        if state == "awaiting_submit":
            browser_page.close()

    result = main.run_form(browser_page, TEST_URL, on_state=stop_before_submit)
    assert result.status == main.RunStatus.UNKNOWN
    assert "awaiting_submit" in states
