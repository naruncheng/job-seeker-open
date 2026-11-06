from __future__ import annotations

import pytest

from jobs import config


@pytest.mark.parametrize("value", ["", "   ", "not-a-number"])
def test_smtp_port_falls_back_to_587_when_blank_or_invalid(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """GitHub Actions passes an unset secret as '' — that must not crash."""
    monkeypatch.setattr(config, "_dotenv_loaded", True)
    monkeypatch.setenv("SMTP_PORT", value)
    assert config.load_secrets().smtp_port == 587


def test_smtp_port_unset_defaults_to_587(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_dotenv_loaded", True)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    assert config.load_secrets().smtp_port == 587


def test_smtp_port_is_read_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_dotenv_loaded", True)
    monkeypatch.setenv("SMTP_PORT", "465")
    assert config.load_secrets().smtp_port == 465
