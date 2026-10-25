"""Unit coverage for the optional Discord notification bridge.

The Discord dependency is intentionally optional for the main form workflow, so
these tests load the bridge with small fakes instead of requiring a Discord
connection or a token.
"""

import asyncio
import importlib
from pathlib import Path
import runpy
import sys
import types
from unittest.mock import AsyncMock, Mock

import pytest


class _FakeIntents:
    default_calls = 0

    @classmethod
    def default(cls):
        cls.default_calls += 1
        return cls()


class _FakeClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.user = None
        self.init_args = args
        self.init_kwargs = kwargs
        self.run_calls = []
        type(self).instances.append(self)

    def run(self, token):
        self.run_calls.append(token)


def _load_bridge_with_fakes():
    fake_discord = types.ModuleType("discord")
    fake_discord.Client = _FakeClient
    fake_discord.Intents = _FakeIntents

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda **_kwargs: None

    old_bridge = sys.modules.get("discord_bridge")
    old_discord = sys.modules.get("discord")
    old_dotenv = sys.modules.get("dotenv")
    sys.modules.pop("discord_bridge", None)
    sys.modules["discord"] = fake_discord
    sys.modules["dotenv"] = fake_dotenv
    try:
        bridge = importlib.import_module("discord_bridge")
    finally:
        if old_discord is None:
            sys.modules.pop("discord", None)
        else:
            sys.modules["discord"] = old_discord
        if old_dotenv is None:
            sys.modules.pop("dotenv", None)
        else:
            sys.modules["dotenv"] = old_dotenv
        if old_bridge is not None:
            sys.modules["discord_bridge"] = old_bridge
    return bridge


discord_bridge = _load_bridge_with_fakes()
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class _FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.send = AsyncMock(name="send")


class _FakeMessage:
    def __init__(self, content, channel_id=123, author=None):
        self.content = content
        self.channel = _FakeChannel(channel_id)
        self.author = object() if author is None else author
        self.reply = AsyncMock(name="reply")


def _run(coro):
    return asyncio.run(coro)


def _reset_fake_client_state():
    _FakeIntents.default_calls = 0
    _FakeClient.instances.clear()


def test_run_bot_without_token_does_not_initialize_or_connect(monkeypatch, capsys):
    _reset_fake_client_state()
    monkeypatch.setattr(discord_bridge, "DISCORD_TOKEN", "")

    discord_bridge.run_bot()

    assert "DISCORD_TOKEN" in capsys.readouterr().out
    assert _FakeIntents.default_calls == 0
    assert _FakeClient.instances == []


def test_cli_returns_nonzero_without_token(monkeypatch, capsys):
    fake_discord = types.ModuleType("discord")
    fake_discord.Client = _FakeClient
    fake_discord.Intents = _FakeIntents
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "discord", fake_discord)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(REPOSITORY_ROOT / "discord_bridge.py"), run_name="__main__")

    assert exc_info.value.code == 1
    assert "DISCORD_TOKEN" in capsys.readouterr().out


@pytest.mark.parametrize(
    "content, expected_url",
    [
        (
            "Please fill https://docs.google.com/forms/d/e/FORM123/viewform",
            "https://docs.google.com/forms/d/e/FORM123/viewform",
        ),
        (
            "Please fill https://forms.gle/FORM123",
            "https://forms.gle/FORM123",
        ),
    ],
)
def test_google_forms_url_patterns_reply_once(monkeypatch, capsys, content, expected_url):
    monkeypatch.setattr(discord_bridge, "DISCORD_CHANNEL_ID", "")
    client = discord_bridge.FormFillerClient()
    message = _FakeMessage(content)

    _run(client.on_message(message))

    message.reply.assert_awaited_once()
    assert expected_url in capsys.readouterr().out


def test_bridge_reports_clean_link_without_discord_brackets_or_sentence_punctuation(monkeypatch, capsys):
    monkeypatch.setattr(discord_bridge, "DISCORD_CHANNEL_ID", "")
    message = _FakeMessage("Form: <https://forms.gle/FORM123>.")
    _run(discord_bridge.FormFillerClient().on_message(message))
    message.reply.assert_awaited_once()
    output = capsys.readouterr().out
    assert "URL: https://forms.gle/FORM123\n" in output


@pytest.mark.parametrize(
    "content",
    [
        "http://forms.gle/FORM123",
        "https://example.com/forms/d/e/FORM123/viewform",
        "https://docs.google.com/forms/FORM123/viewform",
        "https://forms.gle/",
        "There is no form link here.",
    ],
)
def test_non_google_forms_urls_are_ignored(monkeypatch, content):
    monkeypatch.setattr(discord_bridge, "DISCORD_CHANNEL_ID", "")
    message = _FakeMessage(content)

    _run(discord_bridge.FormFillerClient().on_message(message))

    message.reply.assert_not_awaited()


def test_channel_filter_ignores_other_channels_and_allows_configured_channel(monkeypatch):
    monkeypatch.setattr(discord_bridge, "DISCORD_CHANNEL_ID", "321")
    client = discord_bridge.FormFillerClient()
    content = "https://forms.gle/FORM123"
    ignored = _FakeMessage(content, channel_id=999)
    accepted = _FakeMessage(content, channel_id=321)

    _run(client.on_message(ignored))
    _run(client.on_message(accepted))

    ignored.reply.assert_not_awaited()
    accepted.reply.assert_awaited_once()


def test_bot_own_messages_are_ignored(monkeypatch):
    monkeypatch.setattr(discord_bridge, "DISCORD_CHANNEL_ID", "")
    client = discord_bridge.FormFillerClient()
    client.user = object()
    message = _FakeMessage("https://forms.gle/FORM123", author=client.user)

    _run(client.on_message(message))

    message.reply.assert_not_awaited()


def test_link_reply_is_manual_and_never_queues_or_submits(monkeypatch):
    monkeypatch.setattr(discord_bridge, "DISCORD_CHANNEL_ID", "")
    workflow = Mock(side_effect=AssertionError("the Discord bridge must not run the form workflow"))
    queue = Mock(side_effect=AssertionError("the Discord bridge must not queue the form"))
    monkeypatch.setattr(discord_bridge, "form_bot", types.SimpleNamespace(main=workflow), raising=False)
    monkeypatch.setattr(discord_bridge, "bulk_runner", types.SimpleNamespace(run=queue), raising=False)
    message = _FakeMessage("New form: https://forms.gle/FORM123")

    _run(discord_bridge.FormFillerClient().on_message(message))

    message.reply.assert_awaited_once()
    instruction = message.reply.await_args.args[0]
    assert "links.txt" in instruction
    assert "python bulk_runner.py" in instruction
    assert "đăng nhập" in instruction.lower()
    assert "bấm Gửi" in instruction
    assert "tự động nộp" not in instruction.lower()
    message.channel.send.assert_not_awaited()
    workflow.assert_not_called()
    queue.assert_not_called()
