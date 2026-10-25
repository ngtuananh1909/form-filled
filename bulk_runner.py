"""Sequential, resumable runner for manually submitted Google Forms."""

import argparse
from contextlib import contextmanager
import fcntl
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import main as form_bot


PROJECT_DIR = Path(__file__).resolve().parent
LINKS_FILE = PROJECT_DIR / "links.txt"
LEGACY_FILE = PROJECT_DIR / "processed_links.txt"
STATE_FILE = PROJECT_DIR / "form_runs.sqlite3"
BLOCKED_STATES = {"unknown", "legacy_unverified", "awaiting_submit"}


class ActiveRunError(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path):
    lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(lock_fd, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ActiveRunError("Một lượt xử lý form khác đang chạy") from exc
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def form_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname != "docs.google.com":
        return None
    match = re.fullmatch(r"/forms/(?:u/\d+/)?d/(?:e/)?([A-Za-z0-9_-]+)/viewform/?", parsed.path)
    return match.group(1) if match else None


def stored_state(path: Path, url: str) -> str | None:
    """Read a URL state without creating or changing a ledger."""
    if not path.exists():
        return None
    database_uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as db:
        try:
            row = db.execute("SELECT state FROM runs WHERE url=?", (url,)).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return None
    return row[0] if row else None


def legacy_contains(path: Path, url: str) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        try:
            if form_bot.validate_form_url(candidate) == url:
                return True
        except ValueError:
            continue
    return False


def load_links(file_path: Path) -> list[str]:
    if not file_path.exists():
        return []
    seen: set[str] = set()
    links: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        url = form_bot.validate_form_url(candidate)
        if url not in seen:
            links.append(url)
            seen.add(url)
    return links


class RunLedger:
    def __init__(self, path: Path | None = None):
        path = STATE_FILE if path is None else path
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs (
            url TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )""")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS form_aliases (url TEXT PRIMARY KEY, form_id TEXT NOT NULL)")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "RunLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def state(self, url: str) -> str | None:
        row = self.db.execute("SELECT state FROM runs WHERE url=?", (url,)).fetchone()
        return row[0] if row else None

    def record(self, url: str, state: str, reason: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute("""INSERT INTO runs (url, state, reason, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET state=excluded.state,
                reason=excluded.reason, updated_at=excluded.updated_at""", (url, state, reason, now))
            form_id = form_id_from_url(url)
            if form_id:
                self.db.execute("INSERT OR REPLACE INTO form_aliases VALUES (?, ?)", (url, form_id))

    def bind_form_id(self, url: str, form_id: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO form_aliases VALUES (?, ?)", (url, form_id))

    def states_for_form_id(self, form_id: str) -> set[str]:
        rows = self.db.execute("""SELECT runs.state FROM runs JOIN form_aliases
            ON runs.url = form_aliases.url WHERE form_aliases.form_id=?""", (form_id,)).fetchall()
        return {state for (state,) in rows}

    def migrate_legacy(self, path: Path | None = None) -> None:
        path = LEGACY_FILE if path is None else path
        if self.db.execute("SELECT 1 FROM metadata WHERE key='legacy_migrated'").fetchone():
            return
        if not path.exists():
            return
        urls: Iterable[str] = path.read_text(encoding="utf-8").splitlines()
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            for line in urls:
                candidate = line.strip()
                if candidate and not candidate.startswith("#"):
                    try:
                        url = form_bot.validate_form_url(candidate)
                    except ValueError:
                        continue
                    self.db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?, ?)",
                                    (url, "legacy_unverified", "Nhập từ processed_links.txt cũ", now))
            self.db.execute("INSERT INTO metadata VALUES ('legacy_migrated', '1')")

    def recover_interrupted(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute("""UPDATE runs SET state='unknown',
                reason='Phiên trước bị gián đoạn; kiểm tra form trước khi chạy lại', updated_at=?
                WHERE state IN ('running', 'awaiting_login', 'awaiting_review', 'awaiting_submit')""", (now,))

    def list_rows(self) -> list[tuple[str, str, str]]:
        return self.db.execute("SELECT url, state, reason FROM runs ORDER BY updated_at DESC").fetchall()


def run(mode: str = "accuracy", repeat: int = 1, links_file: Path | None = None,
        ledger_path: Path | None = None, legacy_file: Path | None = None) -> list[form_bot.RunResult]:
    links_file = LINKS_FILE if links_file is None else links_file
    ledger_path = STATE_FILE if ledger_path is None else ledger_path
    legacy_file = LEGACY_FILE if legacy_file is None else legacy_file
    if mode != "accuracy":
        raise ValueError("Chế độ spam không thuộc workflow gửi thủ công; chỉ hỗ trợ accuracy")
    if repeat != 1:
        raise ValueError("Chế độ accuracy chỉ cho phép --repeat 1")
    if form_bot.HEADLESS:
        raise RuntimeError("Đặt HEADLESS=false để bạn đăng nhập và bấm Gửi")
    links = load_links(links_file)
    if not links:
        print("[INFO] Không có link hợp lệ trong links.txt")
        return []

    results: list[form_bot.RunResult] = []
    with run_lock(ledger_path), RunLedger(ledger_path) as ledger:
        ledger.migrate_legacy(legacy_file)
        ledger.recover_interrupted()
        runnable: list[str] = []
        for url in links:
            state = ledger.state(url)
            if state == "confirmed":
                print(f"[BỎ QUA] Đã xác nhận gửi: {url}")
            elif state in BLOCKED_STATES:
                print(f"[CẦN KIỂM TRA] {state}: {url}; dùng --resolve-url sau khi kiểm tra")
            else:
                runnable.append(url)
        if not runnable:
            return []

        with sync_playwright() as playwright:
            context = form_bot.launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                for index, url in enumerate(runnable, 1):
                    form_id = form_id_from_url(url)
                    if form_id is None:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=60000)
                            form_id = form_id_from_url(page.url)
                        except Exception:
                            # run_form reports navigation failures for this URL.
                            pass
                    if form_id:
                        equivalent_states = ledger.states_for_form_id(form_id)
                        if "confirmed" in equivalent_states:
                            print(f"[BỎ QUA] Biểu mẫu đã được xác nhận qua link khác: {url}")
                            continue
                        if equivalent_states & BLOCKED_STATES:
                            print(f"[CẦN KIỂM TRA] Biểu mẫu có trạng thái chưa rõ qua link khác: {url}")
                            continue
                    print(f"[{index}/{len(runnable)}] {url}")
                    ledger.record(url, "running", "Đang mở form")
                    if form_id:
                        ledger.bind_form_id(url, form_id)
                    result = form_bot.run_form(
                        page, url,
                        on_state=lambda state, reason, current=url: ledger.record(current, state, reason),
                    )
                    ledger.record(url, result.status.value, result.reason)
                    if not form_id:
                        try:
                            form_id = form_id_from_url(page.url)
                        except Exception:
                            pass
                        if form_id:
                            ledger.bind_form_id(url, form_id)
                    results.append(result)
                    print(f"[{result.status.value.upper()}] {result.reason}")
                    if not result.confirmed:
                        print("[DỪNG] Chưa xác nhận gửi; các link tiếp theo chưa chạy.")
                        break
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    return results


def cli() -> int:
    parser = argparse.ArgumentParser(description="Google Form filler với bước Gửi thủ công")
    parser.add_argument("--mode", choices=["accuracy", "spam"], default="accuracy")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--list-status", action="store_true", help="Xem trạng thái đã lưu")
    parser.add_argument("--resolve-url", help="Link cần giải quyết sau khi bạn kiểm tra thủ công")
    parser.add_argument("--as", dest="resolved_state", choices=["confirmed", "queued"],
                        help="Đánh dấu link đã gửi hoặc cho phép chạy lại")
    args = parser.parse_args()

    if args.resolve_url and not args.resolved_state:
        parser.error("--resolve-url cần --as confirmed hoặc --as queued")
    if args.resolved_state and not args.resolve_url:
        parser.error("--as cần --resolve-url")
    if args.list_status and (args.resolve_url or args.resolved_state):
        parser.error("--list-status không thể kết hợp với --resolve-url hoặc --as")
    if args.mode != "accuracy":
        print("[ERROR] Chế độ spam không thuộc workflow gửi thủ công; chỉ hỗ trợ accuracy")
        return 1
    if args.repeat != 1:
        print("[ERROR] Chế độ accuracy chỉ cho phép --repeat 1")
        return 1

    resolved_url: str | None = None
    if args.resolve_url:
        try:
            resolved_url = form_bot.validate_form_url(args.resolve_url)
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            return 1

    try:
        if args.list_status:
            try:
                with run_lock(STATE_FILE), RunLedger() as ledger:
                    ledger.migrate_legacy()
                    ledger.recover_interrupted()
                    rows = ledger.list_rows()
            except ActiveRunError:
                with RunLedger() as ledger:
                    rows = ledger.list_rows()
            for url, state, reason in rows:
                print(f"{state}\t{url}\t{reason}")
            return 0

        if args.resolve_url:
            assert resolved_url is not None
            with run_lock(STATE_FILE):
                state = stored_state(STATE_FILE, resolved_url)
                if state not in BLOCKED_STATES and not (state is None and legacy_contains(LEGACY_FILE, resolved_url)):
                    parser.error("Chỉ giải quyết URL ở trạng thái unknown/legacy_unverified")
                with RunLedger() as ledger:
                    ledger.migrate_legacy()
                    if ledger.state(resolved_url) not in BLOCKED_STATES:
                        parser.error("Chỉ giải quyết URL ở trạng thái unknown/legacy_unverified")
                    ledger.record(resolved_url, args.resolved_state, "Đã được người dùng kiểm tra thủ công")
            print(f"[OK] {resolved_url}: {args.resolved_state}")
            return 0

        if not load_links(LINKS_FILE):
            print("[INFO] Không có link hợp lệ trong links.txt")
            return 0

        results = run(args.mode, args.repeat)
        with RunLedger() as ledger:
            pending = [url for url in load_links(LINKS_FILE) if ledger.state(url) != "confirmed"]
        return 0 if not pending and all(result.confirmed for result in results) else 1
    except (ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
