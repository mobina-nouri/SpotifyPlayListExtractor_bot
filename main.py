"""Entry point: load settings and run the bot."""
from __future__ import annotations

import asyncio
import logging
import sys

from bot import run_bot
from config import Settings


def _setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


async def amain() -> int:
    _setup_logging()

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2

    await run_bot(settings)
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        return 0


if __name__ == "__main__":
    sys.exit(main())