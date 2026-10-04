"""Standalone Spotify test script (no Telegram required).

Usage:
    python test_spotify.py "https://open.spotify.com/playlist/XXXX"

Verifies the Spotify connection before launching the bot.
"""
from __future__ import annotations

import logging
import sys


def _force_utf8_stdout() -> None:
    """Make Persian characters print correctly on the default Windows console."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)


from config import Settings  # noqa: E402
from spotify_client import (  # noqa: E402
    InvalidPlaylistLinkError,
    PlaylistNotFoundError,
    SpotifyConnectionError,
    SpotifyPlaylistError,
    extract_playlist_id,
    fetch_playlist_tracks,
    format_tracks,
)


def main() -> int:
    _force_utf8_stdout()

    if len(sys.argv) < 2:
        print("Usage: python test_spotify.py <playlist_link>")
        print('Example: python test_spotify.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"')
        return 1

    link = sys.argv[1]

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2

    print(f"Link received: {link}")

    try:
        playlist_id = extract_playlist_id(link)
    except InvalidPlaylistLinkError as exc:
        print(f"Invalid link: {exc}")
        return 3

    print(f"Playlist ID: {playlist_id}")
    print("Fetching tracks...")

    try:
        tracks = fetch_playlist_tracks(
            settings.spotify_client_id,
            settings.spotify_client_secret,
            playlist_id,
        )
    except PlaylistNotFoundError as exc:
        print(f"Playlist not found: {exc}")
        return 4
    except SpotifyConnectionError as exc:
        print(f"Connection error: {exc}")
        return 5
    except SpotifyPlaylistError as exc:
        print(f"Spotify error: {exc}")
        return 6

    if not tracks:
        print("Playlist is empty.")
        return 0

    print(f"\nTrack count: {len(tracks)}\n")
    print(format_tracks(tracks))
    return 0


if __name__ == "__main__":
    sys.exit(main())