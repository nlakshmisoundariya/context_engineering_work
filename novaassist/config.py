"""Configuration from the environment and an optional .env file.

All configuration is read here, in one place. Missing required values fail early
with a message naming the variable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_MAX_COMPLETION_TOKENS = 4096


class ConfigError(RuntimeError):
    """A required configuration value is missing or invalid."""


def load_dotenv_once(path: Path | None = None) -> None:
    """Load a .env file (without overriding variables already set)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is in requirements; tolerate its absence in bare test envs
        return
    candidates = [path] if path else [Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"]
    for candidate in candidates:
        if candidate and candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def env(name: str, default: str | None = None) -> str | None:
    """Return a variable, treating empty strings as unset."""
    value = os.environ.get(name, "")
    return value.strip() if value.strip() else default


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    api_key: str | None
    max_completion_tokens: int

    def redacted(self) -> dict:
        """Settings safe to print or trace (the key is never shown)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": "set" if self.api_key else "missing",
            "max_completion_tokens": self.max_completion_tokens,
        }


def llm_settings(require_key: bool = True) -> LLMSettings:
    """Read LLM settings: LLM_PROVIDER, LLM_MODEL, LLM_API_KEY (else OPENAI_API_KEY)."""
    load_dotenv_once()
    provider = (env("LLM_PROVIDER", "openai") or "openai").lower()
    model = env("LLM_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    api_key = env("LLM_API_KEY") or env("OPENAI_API_KEY")
    raw_max = env("LLM_MAX_COMPLETION_TOKENS", str(DEFAULT_MAX_COMPLETION_TOKENS))
    try:
        max_tokens = int(raw_max)
    except ValueError as exc:
        raise ConfigError(f"LLM_MAX_COMPLETION_TOKENS must be an integer, got {raw_max!r}") from exc
    if provider not in ("openai", "fake"):
        raise ConfigError(f"LLM_PROVIDER must be 'openai' or 'fake', got {provider!r}")
    if provider == "openai" and require_key and not api_key:
        raise ConfigError(
            "LLM_API_KEY is not set (OPENAI_API_KEY is also empty). On the hosted workspace the key is "
            "injected by the platform — tell the trainer. Locally, set it in .env or use LLM_PROVIDER=fake."
        )
    return LLMSettings(provider=provider, model=model, api_key=api_key, max_completion_tokens=max_tokens)
