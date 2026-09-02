"""Telegram botunun saf (ağsız) parçaları."""

import os

import pytest

from sportoto.bot.telegram_bot import (
    MISSING_TOKEN_BANNER,
    _auto_refresh_hours,
    _is_number,
    _split,
    _weekly_day,
    _weekly_hour,
)


def test_split_preserves_content_and_respects_limit():
    text = "\n".join(f"satır {i} " + "x" * 50 for i in range(200))
    chunks = _split(text, 500)
    assert "".join(chunks) == text
    assert all(len(c) <= 500 for c in chunks)


def test_split_breaks_oversized_single_line():
    line = "y" * 1300
    chunks = _split(line, 500)
    assert "".join(chunks) == line
    assert all(len(c) <= 500 for c in chunks)


def test_split_leaves_short_text_alone():
    assert _split("merhaba", 500) == ["merhaba"]


@pytest.mark.parametrize(
    "token,expected",
    [("-0.25", True), ("-0,25", True), ("3", True), ("Galatasaray", False), ("", False)],
)
def test_is_number(token, expected):
    assert _is_number(token) is expected


def test_schedule_settings_are_clamped(monkeypatch):
    monkeypatch.setenv("SPORTOTO_WEEKLY_DAY", "99")
    assert _weekly_day() == 6
    monkeypatch.setenv("SPORTOTO_WEEKLY_DAY", "-5")
    assert _weekly_day() == 0
    monkeypatch.setenv("SPORTOTO_WEEKLY_DAY", "abc")
    assert _weekly_day() == 3          # bozuk değerde varsayılana dön

    monkeypatch.setenv("SPORTOTO_WEEKLY_HOUR", "50")
    assert _weekly_hour() == 23
    monkeypatch.delenv("SPORTOTO_WEEKLY_HOUR")
    assert _weekly_hour() == 9


def test_auto_refresh_hours_defaults_and_disable(monkeypatch):
    monkeypatch.delenv("SPORTOTO_AUTO_UPDATE_HOURS", raising=False)
    assert _auto_refresh_hours() == 24.0
    monkeypatch.setenv("SPORTOTO_AUTO_UPDATE_HOURS", "0")
    assert _auto_refresh_hours() == 0.0
    monkeypatch.setenv("SPORTOTO_AUTO_UPDATE_HOURS", "saçma")
    assert _auto_refresh_hours() == 24.0


def test_missing_token_banner_tells_the_user_what_to_do():
    """Jeton eksikken loglarda görünecek metin eyleme dönüştürülebilir olmalı."""
    assert "TELEGRAM_BOT_TOKEN" in MISSING_TOKEN_BANNER
    assert "BotFather" in MISSING_TOKEN_BANNER
    assert "Variables" in MISSING_TOKEN_BANNER


def test_data_dir_prefers_explicit_env(monkeypatch, tmp_path):
    from sportoto.config import _default_data_dir

    monkeypatch.setenv("SPORTOTO_DATA_DIR", str(tmp_path))
    assert _default_data_dir() == tmp_path
    monkeypatch.delenv("SPORTOTO_DATA_DIR")
    # /data yoksa yerel klasöre düşmeli (bu ortamda /data yok)
    assert _default_data_dir() == (
        __import__("pathlib").Path("/data")
        if os.path.isdir("/data") and os.access("/data", os.W_OK)
        else __import__("pathlib").Path("data")
    )


# --- kısayol menüsü ---
def test_keyboard_labels_map_to_real_commands():
    """Tuş takımı etiketleri gerçek komutlara çözülmeli."""
    from sportoto.bot.telegram_bot import BOT_COMMANDS, KEYBOARD_LABELS, MAIN_KEYBOARD

    known = {f"/{name}" for name, _ in BOT_COMMANDS}
    for label, command in KEYBOARD_LABELS.items():
        assert command in known, f"{label} -> {command} tanımlı komut değil"

    # Tuş takımındaki her buton bir etikete karşılık gelmeli
    buttons = [b for row in MAIN_KEYBOARD["keyboard"] for b in row]
    assert set(buttons) == set(KEYBOARD_LABELS)


def test_command_menu_entries_are_well_formed():
    from sportoto.bot.telegram_bot import BOT_COMMANDS

    assert 5 <= len(BOT_COMMANDS) <= 100
    for name, description in BOT_COMMANDS:
        # Telegram kuralları: küçük harf, 1-32 karakter, açıklama 1-256
        assert name == name.lower() and name.isascii()
        assert 1 <= len(name) <= 32
        assert 1 <= len(description) <= 256


class FakeTelegram:
    """Ağa çıkmadan bot akışını sürmek için sahte Telegram API'si."""

    def __init__(self):
        self.calls = []
        self.sent = []

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if method == "sendMessage":
            self.sent.append(params)
            return {"message_id": len(self.sent), "chat": {"id": params["chat_id"]}}
        if method == "getMe":
            return {"username": "test_bot"}
        return {"ok": True}


@pytest.fixture
def bot(tmp_path, monkeypatch):
    from sportoto.bot.telegram_bot import SporTotoBot
    from sportoto.config import load_settings

    settings = load_settings(source="synthetic")
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "b.db"
    settings.ensure_dirs()

    instance = SporTotoBot(settings, "test-token")
    fake = FakeTelegram()
    monkeypatch.setattr(instance, "_call", fake)
    instance.fake = fake
    return instance


def test_start_shows_persistent_keyboard(bot):
    from sportoto.bot.telegram_bot import MAIN_KEYBOARD

    bot.handle({"chat": {"id": 1}, "text": "/start"})
    assert bot.fake.sent
    assert bot.fake.sent[-1]["reply_markup"] == MAIN_KEYBOARD


def test_keyboard_button_text_is_treated_as_command(bot, monkeypatch):
    seen = []
    monkeypatch.setattr(bot, "_cmd_settings", lambda chat_id, state: seen.append(chat_id))
    bot.handle({"chat": {"id": 7}, "text": "⚙️ Ayarlar"})
    assert seen == [7]


def test_settings_menu_offers_budget_and_target_buttons(bot):
    bot.handle({"chat": {"id": 3}, "text": "/ayarlar"})
    markup = bot.fake.calls[-1][1]["reply_markup"]
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert any(d.startswith("butce:") for d in data)
    assert any(d.startswith("hedef:") for d in data)


def test_budget_button_updates_state_and_refreshes_menu(bot):
    bot.handle({"chat": {"id": 4}, "text": "/ayarlar"})
    bot.handle_callback({
        "id": "q1", "data": "butce:2500",
        "message": {"message_id": 9, "chat": {"id": 4}},
    })
    assert bot.states[4].budget == 2500
    methods = [m for m, _ in bot.fake.calls]
    assert "answerCallbackQuery" in methods
    assert "editMessageReplyMarkup" in methods


def test_target_button_updates_state(bot):
    bot.handle({"chat": {"id": 5}, "text": "/ayarlar"})
    bot.handle_callback({
        "id": "q2", "data": "hedef:13",
        "message": {"message_id": 2, "chat": {"id": 5}},
    })
    assert bot.states[5].target == 13


def test_noop_button_is_ignored(bot):
    bot.handle_callback({
        "id": "q3", "data": "noop", "message": {"message_id": 1, "chat": {"id": 6}},
    })
    assert not bot.fake.sent          # sadece answerCallbackQuery


def test_register_commands_sets_menu(bot):
    from sportoto.bot.telegram_bot import BOT_COMMANDS

    bot._register_commands()
    methods = {m: p for m, p in bot.fake.calls}
    assert "setMyCommands" in methods
    assert len(methods["setMyCommands"]["commands"]) == len(BOT_COMMANDS)
    assert methods["setChatMenuButton"]["menu_button"] == {"type": "commands"}


def test_long_message_attaches_markup_only_once(bot):
    from sportoto.bot.telegram_bot import MAIN_KEYBOARD

    bot.send(1, "x" * 9000, markup=MAIN_KEYBOARD)
    with_markup = [m for m in bot.fake.sent if "reply_markup" in m]
    assert len(bot.fake.sent) > 1
    assert len(with_markup) == 1


def test_unknown_text_gets_a_helpful_answer(bot):
    bot.handle({"chat": {"id": 8}, "text": "merhaba"})
    assert "yardim" in bot.fake.sent[-1]["text"].lower()
