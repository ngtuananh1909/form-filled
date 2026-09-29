import sqlite3
import sys
import fcntl
import os
from pathlib import Path

import pytest

import bulk_runner
import main


FORM_A = "https://docs.google.com/forms/d/e/AAA/viewform"
FORM_B = "https://docs.google.com/forms/d/e/BBB/viewform"


def _configure_cli(monkeypatch, tmp_path: Path, links: list[str] = ()) -> tuple[Path, Path, Path]:
    state_file = tmp_path / "runs.sqlite3"
    links_file = tmp_path / "links.txt"
    legacy_file = tmp_path / "processed_links.txt"
    links_file.write_text("\n".join(links) + ("\n" if links else ""), encoding="utf-8")
    monkeypatch.setattr(bulk_runner, "STATE_FILE", state_file)
    monkeypatch.setattr(bulk_runner, "LINKS_FILE", links_file)
    monkeypatch.setattr(bulk_runner, "LEGACY_FILE", legacy_file)
    monkeypatch.setattr(main, "HEADLESS", False)
    return state_file, links_file, legacy_file


def _run_cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["bulk_runner.py", *args])
    return bulk_runner.cli()


def _assert_no_browser(monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("CLI unexpectedly started a browser")

    monkeypatch.setattr(bulk_runner, "sync_playwright", fail)
    monkeypatch.setattr(main, "launch_context", fail)


class _FakeContext:
    pages = [object()]

    def close(self):
        pass


class _FakePlaywright:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


def _stub_form_run(monkeypatch, calls: list[str]):
    monkeypatch.setattr(bulk_runner, "sync_playwright", lambda: _FakePlaywright())
    monkeypatch.setattr(main, "launch_context", lambda _playwright: _FakeContext())

    def fake_run(_page, url, on_state):
        calls.append(url)
        on_state("awaiting_review", "reviewed")
        return main.RunResult(main.RunStatus.CONFIRMED, url, "confirmed")

    monkeypatch.setattr(main, "run_form", fake_run)


def test_cli_help_exits_zero_without_opening_a_browser(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bulk_runner.py", "--help"])

    with pytest.raises(SystemExit) as raised:
        bulk_runner.cli()

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    assert "--list-status" in output
    assert "--resolve-url" in output


def test_cli_list_status_recovers_interrupted_rows_in_isolated_ledger(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "running", "opening")
        ledger.record(FORM_B, "awaiting_submit", "waiting for submit")

    _assert_no_browser(monkeypatch)
    monkeypatch.setattr(bulk_runner, "run", lambda *_args, **_kwargs: pytest.fail("list mode ran forms"))

    assert _run_cli(monkeypatch, "--list-status") == 0

    output = capsys.readouterr().out
    assert f"unknown\t{FORM_A}" in output
    assert f"unknown\t{FORM_B}" in output
    assert "Phiên trước bị gián đoạn" in output
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "unknown"
        assert ledger.state(FORM_B) == "unknown"


def test_cli_list_status_does_not_recover_a_live_run(monkeypatch, tmp_path: Path, capsys):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "awaiting_submit", "waiting")
    lock_fd = os.open(str(state_file) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _run_cli(monkeypatch, "--list-status") == 0
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    assert f"awaiting_submit\t{FORM_A}" in capsys.readouterr().out
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "awaiting_submit"


def test_cli_resolve_unknown_as_confirmed_updates_only_isolated_ledger(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "check manually")

    _assert_no_browser(monkeypatch)
    assert _run_cli(monkeypatch, "--resolve-url", FORM_A, "--as", "confirmed") == 0

    assert f"[OK] {FORM_A}: confirmed" in capsys.readouterr().out
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "confirmed"


def test_cli_resolve_unknown_as_queued_leaves_link_runnable(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path, [FORM_A])
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "check manually")

    _assert_no_browser(monkeypatch)
    assert _run_cli(monkeypatch, "--resolve-url", FORM_A, "--as", "queued") == 0

    assert f"[OK] {FORM_A}: queued" in capsys.readouterr().out
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "queued"


def test_cli_rejected_resolve_does_not_recover_unrelated_rows(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "confirmed", "already sent")
        ledger.record(FORM_B, "awaiting_submit", "leave for review")
    before = state_file.read_bytes()
    _assert_no_browser(monkeypatch)

    with pytest.raises(SystemExit) as raised:
        _run_cli(monkeypatch, "--resolve-url", FORM_A, "--as", "queued")

    assert raised.value.code == 2
    assert "Chỉ giải quyết URL" in capsys.readouterr().err
    assert state_file.read_bytes() == before
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "confirmed"
        assert ledger.state(FORM_B) == "awaiting_submit"


def test_cli_rejected_resolve_does_not_migrate_unrelated_legacy_rows(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, legacy_file = _configure_cli(monkeypatch, tmp_path)
    legacy_file.write_text(f"{FORM_B}\n", encoding="utf-8")
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "confirmed", "already sent")

    before_bytes = state_file.read_bytes()
    with sqlite3.connect(state_file) as db:
        before_runs = db.execute("SELECT url, state, reason FROM runs ORDER BY url").fetchall()
        before_metadata = db.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    _assert_no_browser(monkeypatch)

    with pytest.raises(SystemExit) as raised:
        _run_cli(monkeypatch, "--resolve-url", FORM_A, "--as", "queued")

    assert raised.value.code == 2
    assert "Chỉ giải quyết URL" in capsys.readouterr().err
    assert state_file.read_bytes() == before_bytes
    with sqlite3.connect(state_file) as db:
        assert db.execute("SELECT url, state, reason FROM runs ORDER BY url").fetchall() == before_runs
        assert db.execute("SELECT key, value FROM metadata ORDER BY key").fetchall() == before_metadata


def test_cli_rejected_resolve_for_unknown_url_does_not_create_a_ledger(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    _assert_no_browser(monkeypatch)

    with pytest.raises(SystemExit) as raised:
        _run_cli(monkeypatch, "--resolve-url", FORM_A, "--as", "confirmed")

    assert raised.value.code == 2
    assert "Chỉ giải quyết URL" in capsys.readouterr().err
    assert not state_file.exists()


def test_cli_resolve_migrates_and_resolves_target_present_in_legacy_source(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, legacy_file = _configure_cli(monkeypatch, tmp_path)
    legacy_file.write_text(f"{FORM_A}\n", encoding="utf-8")
    _assert_no_browser(monkeypatch)

    assert _run_cli(monkeypatch, "--resolve-url", FORM_A, "--as", "confirmed") == 0

    assert f"[OK] {FORM_A}: confirmed" in capsys.readouterr().out
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "confirmed"


def test_cli_resume_skips_unknown_and_returns_failure_until_all_links_are_confirmed(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path, [FORM_A, FORM_B])
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "check manually")
    calls: list[str] = []
    _stub_form_run(monkeypatch, calls)

    assert _run_cli(monkeypatch) == 1

    output = capsys.readouterr().out
    assert f"[CẦN KIỂM TRA] unknown: {FORM_A}" in output
    assert calls == [FORM_B]
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "unknown"
        assert ledger.state(FORM_B) == "confirmed"


def test_cli_confirmed_run_returns_zero_and_records_state(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path, [FORM_A])
    calls: list[str] = []
    _stub_form_run(monkeypatch, calls)

    assert _run_cli(monkeypatch) == 0

    assert calls == [FORM_A]
    assert "[CONFIRMED] confirmed" in capsys.readouterr().out
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "confirmed"


def test_run_uses_explicit_legacy_file_with_explicit_paths(monkeypatch, tmp_path: Path):
    links_file = tmp_path / "links.txt"
    legacy_file = tmp_path / "processed_links.txt"
    state_file = tmp_path / "runs.sqlite3"
    links_file.write_text(f"{FORM_A}\n", encoding="utf-8")
    legacy_file.write_text(f"{FORM_B}\n", encoding="utf-8")
    monkeypatch.setattr(main, "HEADLESS", False)
    calls: list[str] = []
    _stub_form_run(monkeypatch, calls)

    results = bulk_runner.run(
        links_file=links_file,
        ledger_path=state_file,
        legacy_file=legacy_file,
    )

    assert [result.url for result in results] == [FORM_A]
    assert calls == [FORM_A]
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "confirmed"
        assert ledger.state(FORM_B) == "legacy_unverified"


def test_migrate_legacy_retries_after_source_is_created(tmp_path: Path):
    legacy_file = tmp_path / "processed_links.txt"
    state_file = tmp_path / "runs.sqlite3"
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.migrate_legacy(legacy_file)
        assert ledger.state(FORM_A) is None

    legacy_file.write_text(f"{FORM_A}\n", encoding="utf-8")
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.migrate_legacy(legacy_file)
        assert ledger.state(FORM_A) == "legacy_unverified"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--as", "confirmed"), "--as cần --resolve-url"),
        (("--resolve-url", FORM_A), "--resolve-url cần --as"),
        (("--resolve-url", FORM_A, "--as", "later"), "invalid choice"),
    ],
)
def test_cli_rejects_invalid_argument_combinations(
    monkeypatch, tmp_path: Path, args: tuple[str, ...], message: str, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "keep unchanged")
    before = state_file.read_bytes()
    _assert_no_browser(monkeypatch)

    with pytest.raises(SystemExit) as raised:
        _run_cli(monkeypatch, *args)

    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert state_file.read_bytes() == before
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "unknown"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--mode", "spam"), "Chế độ spam không thuộc workflow"),
        (("--repeat", "2"), "Chế độ accuracy chỉ cho phép --repeat 1"),
    ],
)
def test_cli_rejects_unsupported_run_options_before_opening_ledger(
    monkeypatch, tmp_path: Path, args: tuple[str, ...], message: str, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "keep unchanged")
    before = state_file.read_bytes()
    _assert_no_browser(monkeypatch)

    assert _run_cli(monkeypatch, *args) == 1

    assert message in capsys.readouterr().out
    assert state_file.read_bytes() == before
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "unknown"


def test_cli_rejects_list_and_resolve_combination_before_opening_ledger(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "keep unchanged")
    before = state_file.read_bytes()
    _assert_no_browser(monkeypatch)

    with pytest.raises(SystemExit) as raised:
        _run_cli(
            monkeypatch,
            "--list-status",
            "--resolve-url",
            FORM_A,
            "--as",
            "confirmed",
        )

    assert raised.value.code == 2
    assert "--list-status không thể kết hợp" in capsys.readouterr().err
    assert state_file.read_bytes() == before
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "unknown"


def test_cli_invalid_form_url_returns_one_without_opening_a_browser(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_A, "unknown", "keep unchanged")
    before = state_file.read_bytes()
    _assert_no_browser(monkeypatch)

    assert _run_cli(
        monkeypatch,
        "--resolve-url",
        "https://example.com/not-a-form",
        "--as",
        "confirmed",
    ) == 1

    assert "[ERROR]" in capsys.readouterr().out
    assert state_file.read_bytes() == before
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_A) == "unknown"


def test_cli_empty_links_returns_zero_without_opening_a_browser(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path)
    _assert_no_browser(monkeypatch)

    assert _run_cli(monkeypatch) == 0
    assert "Không có link hợp lệ" in capsys.readouterr().out
    assert not state_file.exists()


def test_cli_headless_rejection_does_not_mutate_ledger(
    monkeypatch, tmp_path: Path, capsys
):
    state_file, _links_file, _legacy_file = _configure_cli(monkeypatch, tmp_path, [FORM_A])
    with bulk_runner.RunLedger(state_file) as ledger:
        ledger.record(FORM_B, "awaiting_submit", "keep unchanged")
    before = state_file.read_bytes()
    monkeypatch.setattr(main, "HEADLESS", True)
    _assert_no_browser(monkeypatch)

    assert _run_cli(monkeypatch) == 1

    assert "Đặt HEADLESS=false" in capsys.readouterr().out
    assert state_file.read_bytes() == before
    with bulk_runner.RunLedger(state_file) as ledger:
        assert ledger.state(FORM_B) == "awaiting_submit"
