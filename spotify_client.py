"""Spotify client that fetches playlist tracks.

Uses Spotify's public embed page to avoid OAuth, Client Credentials,
and the Premium requirement of the official Web API.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PLAYLIST_LINK_REGEX = re.compile(
    r"(?:https?://open\.spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]{22})"
)

_PAGE_SIZE = 100
_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 1.5

_EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
_API_URL = "https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Spotify ships up to ~100 tracks in the embed page payload.
# If we get this many, there's likely more in the playlist.
_EMBED_BATCH_THRESHOLD = 100


@dataclass
class Track:
    """A single track for display."""
    name: str
    artists: str
    available: bool


class SpotifyPlaylistError(Exception):
    """Base error for playlist issues."""


class InvalidPlaylistLinkError(SpotifyPlaylistError):
    """The provided link is not a valid Spotify playlist link."""


class PlaylistNotFoundError(SpotifyPlaylistError):
    """Playlist not found or is private."""


class SpotifyConnectionError(SpotifyPlaylistError):
    """Network failure or exhausted retries against Spotify."""


def extract_playlist_id(text: str) -> str:
    """Extract the playlist ID from a Spotify URL or URI.

    Accepted formats:
    - https://open.spotify.com/playlist/XXXX?si=...
    - https://open.spotify.com/playlist/XXXX
    - spotify:playlist:XXXX
    """
    if not text:
        raise InvalidPlaylistLinkError("The playlist link is empty.")

    match = _PLAYLIST_LINK_REGEX.search(text.strip())
    if not match:
        raise InvalidPlaylistLinkError(
            "Invalid link. Please send a valid Spotify playlist link."
        )
    return match.group(1)


def format_tracks(tracks: list[Track]) -> str:
    """Format a list of tracks as a numbered plain-text list."""
    lines: list[str] = []
    counter = 0
    for t in tracks:
        counter += 1
        if not t.available:
            lines.append(f"{counter}. [Unavailable]")
        else:
            lines.append(f"{counter}. {t.name} - {t.artists}")
    return "\n".join(lines)


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)
_ACCESS_TOKEN_RE = re.compile(r'"accessToken":"([^"]{20,})"')


def _fetch_embed_page(playlist_id: str) -> tuple[str, list[dict]]:
    """Fetch the public embed page and return (access_token, initial_track_list)."""
    url = _EMBED_URL.format(playlist_id=playlist_id)

    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": _USER_AGENT}) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise SpotifyConnectionError(
            "Could not reach Spotify. Please try again later."
        ) from exc

    if response.status_code == 404:
        raise PlaylistNotFoundError(
            "Playlist not found or is private. Only public playlists are supported."
        )
    if response.status_code >= 400:
        raise SpotifyConnectionError(
            f"HTTP {response.status_code} while fetching the playlist."
        )

    html = response.text

    access_token_match = _ACCESS_TOKEN_RE.search(html)
    if not access_token_match:
        raise SpotifyConnectionError(
            "Could not find an access token in the embed page. "
            "Spotify may have changed the page structure."
        )
    access_token = access_token_match.group(1)

    next_data_match = _NEXT_DATA_RE.search(html)
    if not next_data_match:
        raise SpotifyConnectionError("Could not find playlist data in the embed page.")

    try:
        payload = json.loads(next_data_match.group(1))
        tracks_raw = (
            payload["props"]["pageProps"]["state"]["data"]["entity"]
            .get("trackList", [])
        )
    except (json.JSONDecodeError, KeyError) as exc:
        raise SpotifyConnectionError(
            "The embed page structure has changed. Please update the bot."
        ) from exc

    return access_token, tracks_raw


def _convert_embed_tracks(raw_tracks: list[dict]) -> list[Track]:
    """Convert embed page trackList items into Track objects."""
    tracks: list[Track] = []
    for t in raw_tracks:
        if t.get("entityType") and t["entityType"] != "track":
            continue
        title = (t.get("title") or "").strip() or "[Untitled]"
        artist = (t.get("subtitle") or "").strip()
        if not artist or artist == "Spotify":
            artist = "[Unknown]"
        tracks.append(Track(name=title, artists=artist, available=True))
    return tracks


def _is_retryable_http_status(status: int) -> bool:
    """429 (rate limit) and 5xx (server errors) are retryable."""
    return status == 429 or 500 <= status < 600


def _fetch_remaining_tracks(
    access_token: str,
    playlist_id: str,
    already_have: int,
) -> list[dict]:
    """Fetch tracks beyond the embed batch using the access token.

    Falls back to returning what we have on rate limit or auth failure.
    """
    results: list[dict] = []
    offset = already_have
    url = _API_URL.format(playlist_id=playlist_id)

    while True:
        attempt = 0
        page_data: dict[str, Any] | None = None

        while attempt < _MAX_RETRIES:
            try:
                with httpx.Client(timeout=15.0) as client:
                    response = client.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "User-Agent": _USER_AGENT,
                        },
                        params={
                            "limit": _PAGE_SIZE,
                            "offset": offset,
                            "additional_types": "track",
                        },
                    )
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt >= _MAX_RETRIES:
                    raise SpotifyConnectionError(
                        "Lost connection to the Spotify API."
                    ) from exc
                wait = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning("Network error, retrying in %.1fs: %s", wait, exc)
                time.sleep(wait)
                continue

            if response.status_code in (401, 403):
                logger.warning("Auth error %s from Spotify API.", response.status_code)
                return results
            if response.status_code == 404:
                raise PlaylistNotFoundError("Playlist not found or is private.")
            if _is_retryable_http_status(response.status_code):
                attempt += 1
                retry_after = response.headers.get("Retry-After")
                wait_seconds: float | None = None
                if retry_after and retry_after.isdigit():
                    wait_seconds = float(retry_after)

                # If Spotify says wait for hours, just give up on pagination
                # and return what we have from the embed page.
                if wait_seconds is not None and wait_seconds > 60:
                    logger.warning(
                        "Spotify rate limit Retry-After=%ss; skipping pagination.",
                        int(wait_seconds),
                    )
                    return results

                if attempt >= _MAX_RETRIES:
                    raise SpotifyConnectionError(
                        f"Spotify API returned {response.status_code}."
                    )
                wait = wait_seconds if wait_seconds is not None else _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning("HTTP %s, retrying in %.1fs", response.status_code, wait)
                time.sleep(wait)
                continue

            try:
                page_data = response.json()
            except json.JSONDecodeError as exc:
                raise SpotifyConnectionError("Invalid response from Spotify API.") from exc
            break

        if page_data is None:
            break

        items = page_data.get("items", []) or []
        results.extend(items)

        if len(items) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    return results


def _convert_api_tracks(api_items: list[dict]) -> list[Track]:
    """Convert Web API playlist-items response into Track objects."""
    tracks: list[Track] = []
    for item in api_items:
        if not isinstance(item, dict):
            continue
        track = item.get("track")
        if not track or track.get("is_local"):
            tracks.append(Track(name="", artists="", available=False))
            continue
        if track.get("type") != "track":
            continue

        name = (track.get("name") or "").strip() or "[Untitled]"
        artists_raw = track.get("artists") or []
        artist_names = [a.get("name", "").strip() for a in artists_raw if a.get("name")]
        artists_str = ", ".join(filter(None, artist_names)) or "[Unknown]"
        tracks.append(Track(name=name, artists=artists_str, available=True))
    return tracks


def fetch_playlist_tracks(
    client_id: str | None,
    client_secret: str | None,
    playlist_id: str,
) -> list[Track]:
    """Fetch all tracks of a playlist.

    `client_id` and `client_secret` are kept for backward compatibility
    but are no longer required.
    """
    access_token, initial_raw = _fetch_embed_page(playlist_id)
    tracks = _convert_embed_tracks(initial_raw)

    if len(initial_raw) >= _EMBED_BATCH_THRESHOLD:
        try:
            remaining = _fetch_remaining_tracks(
                access_token, playlist_id, already_have=len(initial_raw)
            )
            tracks.extend(_convert_api_tracks(remaining))
        except (PlaylistNotFoundError, SpotifyConnectionError) as exc:
            logger.warning(
                "Could not fetch remaining tracks; returning the first %s: %s",
                len(tracks), exc,
            )

    return tracks