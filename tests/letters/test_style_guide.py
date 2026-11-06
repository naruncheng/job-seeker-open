from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jobs.config import Secrets
from jobs.letters import style_guide as style_guide_module

SECRETS_WITH_KEY = Secrets(
    anthropic_api_key="fake-key",
    adzuna_app_id=None,
    adzuna_app_key=None,
    discord_webhook_url=None,
    smtp_host=None,
    smtp_port=587,
    smtp_user=None,
    smtp_pass=None,
    digest_to_email=None,
    digest_from_email=None,
    user_agent_email=None,
)

SECRETS_NO_KEY = Secrets(
    anthropic_api_key=None,
    adzuna_app_id=None,
    adzuna_app_key=None,
    discord_webhook_url=None,
    smtp_host=None,
    smtp_port=587,
    smtp_user=None,
    smtp_pass=None,
    digest_to_email=None,
    digest_from_email=None,
    user_agent_email=None,
)


class FakeMessages:
    def create(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="# Style guide\n\nSpecific and quoted.")],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


class FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.messages = FakeMessages()


class FakeAnthropicModule:
    Anthropic = FakeClient


def _setup_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(style_guide_module, "STYLE_GUIDE_PATH", tmp_path / "style_guide.md")
    monkeypatch.setattr(style_guide_module, "RESUME_PATH", tmp_path / "resume.txt")
    monkeypatch.setattr(style_guide_module, "COVER_LETTERS_DIR", tmp_path / "cover_letters")


def test_run_style_guide_skips_without_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(style_guide_module, "load_secrets", lambda: SECRETS_NO_KEY)

    result = style_guide_module.run_style_guide()

    assert result.generated is False
    assert result.skipped_reason == "missing ANTHROPIC_API_KEY"


def test_run_style_guide_skips_without_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(style_guide_module, "load_secrets", lambda: SECRETS_WITH_KEY)

    result = style_guide_module.run_style_guide()

    assert result.generated is False
    assert "no resume" in (result.skipped_reason or "")


def test_run_style_guide_skips_without_cover_letters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(style_guide_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    (tmp_path / "resume.txt").write_text("resume content", encoding="utf-8")

    result = style_guide_module.run_style_guide()

    assert result.generated is False
    assert "no past cover letters" in (result.skipped_reason or "")


def _setup_valid_inputs(tmp_path: Path) -> None:
    (tmp_path / "resume.txt").write_text("resume content", encoding="utf-8")
    letters_dir = tmp_path / "cover_letters"
    letters_dir.mkdir()
    (letters_dir / "letter1.txt").write_text("Dear hiring manager...", encoding="utf-8")


def test_run_style_guide_generates_and_writes_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(style_guide_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(style_guide_module, "anthropic", FakeAnthropicModule())
    _setup_valid_inputs(tmp_path)

    result = style_guide_module.run_style_guide()

    assert result.generated is True
    assert result.letters_used == 1
    assert result.spend_usd > 0
    written = (tmp_path / "style_guide.md").read_text(encoding="utf-8")
    assert "Style guide" in written


def test_run_style_guide_refuses_to_overwrite_without_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(style_guide_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    existing = tmp_path / "style_guide.md"
    existing.write_text("hand-tweaked content", encoding="utf-8")

    result = style_guide_module.run_style_guide(force=False)

    assert result.generated is False
    assert "--force" in (result.skipped_reason or "")
    assert existing.read_text(encoding="utf-8") == "hand-tweaked content"


def test_run_style_guide_force_backs_up_existing_before_overwriting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(style_guide_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(style_guide_module, "anthropic", FakeAnthropicModule())
    _setup_valid_inputs(tmp_path)
    existing = tmp_path / "style_guide.md"
    existing.write_text("hand-tweaked content", encoding="utf-8")

    result = style_guide_module.run_style_guide(force=True)

    assert result.generated is True
    backups = list(tmp_path.glob("style_guide.md.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "hand-tweaked content"
    assert "Style guide" in existing.read_text(encoding="utf-8")
