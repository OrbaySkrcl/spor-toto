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
