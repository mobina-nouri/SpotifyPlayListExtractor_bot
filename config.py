"""Configuration loader and validator."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _read_env(key: str) -> str | None:
    """Read an env var and strip whitespace; return None if empty."""
    value = os.getenv(key)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


@dataclass(frozen=True)
class Settings:
    """Application settings."""
    bot_token: str
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None

    @staticmethod
    def from_env() -> "Settings":
        """Build Settings from environment. Raises ValueError if BOT_TOKEN is missing."""
        bot_token = _read_env("BOT_TOKEN")
        if not bot_token:
            raise ValueError(
                "BOT_TOKEN is not set. Please create a .env file based on .env.example."
            )

        return Settings(
            bot_token=bot_token,  # type: ignore[arg-type]
            spotify_client_id=_read_env("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=_read_env("SPOTIFY_CLIENT_SECRET"),
        )