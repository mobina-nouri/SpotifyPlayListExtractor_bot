"""Telegram bot for extracting Spotify playlist tracks.

The playlist list is rendered with selectable <code> entities. Users can
also tap an inline button to copy a single track by entering its number,
or tap a persistent keyboard button to start a new playlist request.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Final

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Settings
from spotify_client import (
    InvalidPlaylistLinkError,
    PlaylistNotFoundError,
    SpotifyConnectionError,
    SpotifyPlaylistError,
    Track,
    extract_playlist_id,
    fetch_playlist_tracks,
)

logger = logging.getLogger(__name__)

# Maximum length for a single Telegram message
SAFE_CHUNK_SIZE: Final = 4000

# Telegram caps message entities at 100; we use one <code> entity per track.
MAX_ENTITIES_PER_MESSAGE: Final = 100

# Callback data prefixes
CB_COPY_MODE: Final = "cm"   # enter "copy by number" mode
CB_NEW_PLAYLIST: Final = "np"  # acknowledge the persistent keyboard button

# User data keys
UD_TRACKS: Final = "current_tracks"
UD_AWAITING_NUMBER: Final = "awaiting_track_number"


# ---------------- User-facing messages ----------------

WELCOME_MESSAGE: Final = (
    "🎵 <b>Welcome to the Spotify Playlist Extractor bot!</b>\n\n"
    "Send me any public Spotify playlist link and I'll fetch the full list "
    "of tracks with their artists.\n\n"
    "📌 Accepted formats:\n"
    "• <code>https://open.spotify.com/playlist/...</code>\n"
    "• <code>spotify:playlist:...</code>\n\n"
    "ℹ️ Send /help to see usage instructions."
)

HELP_MESSAGE: Final = (
    "📖 <b>How to use</b>\n\n"
    "1. Copy a link to a public Spotify playlist.\n"
    "2. Send the link to this bot.\n"
    "3. Wait a moment for the track list to be sent.\n"
    "4. To copy a single track, tap <b>📋 Copy by number</b> and send the track number.\n"
    "5. To send a new playlist, tap <b>📨 Send new playlist</b> at the bottom of the chat.\n\n"
    "⚠️ Only <b>public</b> playlists are supported.\n"
    "⚠️ Private playlists are not accessible.\n\n"
    "🛠 Commands:\n"
    "• /start - show the welcome message\n"
    "• /help - show this help message"
)

NO_LINK_HINT: Final = (
    "🔎 Please send a <b>Spotify playlist link</b>.\n"
    "Send /help for instructions."
)

EMPTY_PLAYLIST: Final = (
    "📭 This playlist is empty or has no readable tracks."
)

NEW_PLAYLIST_PROMPT: Final = (
    "📨 <b>Waiting for a new playlist link...</b>\n\n"
    "Send any Spotify playlist link:"
)

ASK_TRACK_NUMBER: Final = (
    "🔢 <b>Send the track number you want to copy:</b>\n\n"
    "Send /cancel to abort."
)

CANCELLED: Final = "✅ Cancelled."

BAD_NUMBER: Final = "❌ Invalid number. Send a number between 1 and {total}."

NO_TRACKS_IN_MEMORY: Final = (
    "❌ No playlist in memory. Send a playlist link first."
)

SETTINGS_NOT_LOADED: Final = (
    "⚠️ Bot settings not loaded. Please check your .env file."
)

FETCHING: Final = "⏳ Fetching playlist info..."

SEND_ANOTHER_HINT: Final = (
    "Send another number for another track, or send a new playlist link."
)


# When a user sends a bare number without tapping "Copy by number" first, we
# still try to resolve it against the most recently fetched playlist.
# If no playlist has been loaded yet, we point them at the button instead.
NUMBER_WITHOUT_CONTEXT: Final = (
    "🔢 Send a track number to copy it from the last playlist.\n\n"
    "Or tap <b>📋 Copy by number</b> below the playlist first."
)


# ---------------- Persistent keyboard (bottom buttons) ----------------

MAIN_KEYBOARD: Final = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📨 Send new playlist")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Send a Spotify playlist link here...",
)


# ---------------- Helpers ----------------


def _track_text(idx: int, track: Track) -> str:
    """Format a single track with its 1-based number."""
    if not track.available:
        return f"{idx}. [Unavailable]"
    return f"{idx}. {track.name} - {track.artists}"


def _build_track_message(
    tracks: list[Track],
    header: str,
) -> tuple[str, list[MessageEntity]]:
    """Build a message where each track is wrapped in its own inline <code> entity.

    On Telegram, each <code> entity renders as a small monospace block that
    can be long-pressed on mobile (or drag-selected on desktop) to copy it.
    """
    blocks: list[str] = []
    entities: list[MessageEntity] = []
    pos = len(header.encode("utf-16-le")) // 2

    for offset, t in enumerate(tracks):
        line = _track_text(offset + 1, t)
        line_with_break = f"{line}\n"
        line_len = len(line.encode("utf-16-le")) // 2
        total_len = len(line_with_break.encode("utf-16-le")) // 2

        entities.append(
            MessageEntity(type="code", offset=pos, length=line_len)
        )
        blocks.append(line_with_break)
        pos += total_len

    return header + "".join(blocks), entities


def _build_header(playlist_id: str, total_tracks: int, page_num: int = 1) -> str:
    if page_num == 1:
        return (
            f"🎶 <b>Total tracks:</b> {total_tracks}\n"
            f"🔗 <b>Playlist ID:</b> <code>{playlist_id}</code>\n\n"
        )
    return f"📄 <b>Continued (page {page_num}):</b>\n\n"


def _split_tracks_into_messages(
    tracks: list[Track],
    playlist_id: str,
) -> list[tuple[str, list[MessageEntity]]]:
    """Split tracks into messages respecting entity and length caps."""
    messages: list[tuple[str, list[MessageEntity]]] = []
    remaining = tracks
    page_num = 1

    while remaining:
        batch_size = min(len(remaining), MAX_ENTITIES_PER_MESSAGE)
        while batch_size > 0:
            header = _build_header(playlist_id, len(tracks), page_num)
            text, entities = _build_track_message(remaining[:batch_size], header)
            if len(text.encode("utf-16-le")) // 2 <= SAFE_CHUNK_SIZE:
                break
            batch_size = max(1, batch_size // 2)

        batch = remaining[:batch_size]
        header = _build_header(playlist_id, len(tracks), page_num)
        text, entities = _build_track_message(batch, header)
        messages.append((text, entities))
        remaining = remaining[batch_size:]
        page_num += 1

    return messages


def _result_inline_markup() -> InlineKeyboardMarkup:
    """Inline button shown beneath the playlist result."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Copy by number", callback_data=CB_COPY_MODE)],
    ])


# ---------------- Handlers ----------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /start with the welcome message and persistent keyboard."""
    if update.effective_message:
        await update.effective_message.reply_text(
            WELCOME_MESSAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /help."""
    if update.effective_message:
        await update.effective_message.reply_text(
            HELP_MESSAGE, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
        )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route inline button callbacks."""
    query = update.callback_query
    if not query or not query.data:
        return

    if query.data == CB_COPY_MODE:
        await query.answer()
        tracks: list[Track] | None = context.user_data.get(UD_TRACKS)  # type: ignore[assignment]
        if not tracks:
            logger.warning(
                "Copy-by-number tapped but no tracks in user_data. "
                "user_data keys: %s", list(context.user_data.keys()),
            )
            await query.message.reply_text(  # type: ignore[union-attr]
                NO_TRACKS_IN_MEMORY
            )
            return
        context.user_data[UD_AWAITING_NUMBER] = True  # type: ignore[index]
        await query.message.reply_text(  # type: ignore[union-attr]
            ASK_TRACK_NUMBER, parse_mode=ParseMode.HTML
        )
        return


async def _send_track_by_number(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    number_text: str,
) -> bool:
    """Send the requested track as a separate message and stay in number-mode.

    Returns True if the input was handled (success or a friendly error),
    False only when there's no playlist loaded at all (caller should reply).
    """
    tracks: list[Track] | None = context.user_data.get(UD_TRACKS)  # type: ignore[assignment]
    if not tracks:
        # Caller will surface NUMBER_WITHOUT_CONTEXT (or NO_TRACKS_IN_MEMORY).
        context.user_data[UD_AWAITING_NUMBER] = False  # type: ignore[index]
        return False

    try:
        idx = int(number_text.strip())
    except ValueError:
        await message.reply_text(
            BAD_NUMBER.format(total=len(tracks)),
            parse_mode=ParseMode.HTML,
        )
        return True  # stay in number mode

    if not (1 <= idx <= len(tracks)):
        await message.reply_text(
            BAD_NUMBER.format(total=len(tracks)),
            parse_mode=ParseMode.HTML,
        )
        return True  # stay in number mode

    track = tracks[idx - 1]
    text = _track_text(idx, track)
    await message.reply_text(
        f"🎵 <b>Track #{idx}:</b>\n\n<code>{text}</code>",
        parse_mode=ParseMode.HTML,
    )
    # Intentionally do NOT reset UD_AWAITING_NUMBER here so the user can
    # keep requesting more tracks. They leave number-mode by sending a
    # new playlist link, the "Send new playlist" button, or /cancel.
    return True


async def _handle_playlist_request(
    message: Message,
    settings: Settings,
    link: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Fetch tracks for a playlist link and send them as a selectable list."""
    status_msg = await message.reply_text(FETCHING)

    try:
        await message.chat.send_action(ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass

    loop = asyncio.get_running_loop()

    try:
        playlist_id = await loop.run_in_executor(None, extract_playlist_id, link)

        tracks: list[Track] = await loop.run_in_executor(
            None,
            fetch_playlist_tracks,
            settings.spotify_client_id,
            settings.spotify_client_secret,
            playlist_id,
        )

        if not tracks:
            await status_msg.edit_text(EMPTY_PLAYLIST)
            return

        # Save the fetched tracks on the user so the "copy by number" button can find them.
        context.user_data[UD_TRACKS] = tracks  # type: ignore[index]
        context.user_data[UD_AWAITING_NUMBER] = False  # type: ignore[index]

        messages = _split_tracks_into_messages(tracks, playlist_id)
        first_text, first_entities = messages[0]
        await status_msg.edit_text(
            text=first_text,
            entities=first_entities,
            parse_mode=ParseMode.HTML,
            reply_markup=_result_inline_markup(),
        )
        for text, entities in messages[1:]:
            await message.reply_text(
                text=text, entities=entities, parse_mode=ParseMode.HTML
            )

    except InvalidPlaylistLinkError as exc:
        await status_msg.edit_text(f"❌ {exc}")
    except PlaylistNotFoundError as exc:
        await status_msg.edit_text(f"❌ {exc}")
    except SpotifyConnectionError as exc:
        await status_msg.edit_text(f"❌ {exc}")
    except SpotifyPlaylistError as exc:
        logger.exception("Unexpected Spotify error")
        await status_msg.edit_text(f"❌ Unexpected error: {exc}")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch a playlist request: route by persistent keyboard, number-mode, or link."""
    message = update.effective_message
    if not message or not message.text:
        return

    text = message.text.strip()
    settings: Settings | None = context.application.bot_data.get("settings")  # type: ignore[assignment]

    if not settings:
        await message.reply_text(SETTINGS_NOT_LOADED)
        return

    # Persistent keyboard button → "send a new playlist"
    if text == "📨 Send new playlist":
        context.user_data.pop(UD_TRACKS, None)  # type: ignore[arg-type]
        context.user_data[UD_AWAITING_NUMBER] = False  # type: ignore[index]
        await message.reply_text(
            NEW_PLAYLIST_PROMPT,
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        # Re-show the keyboard so the user still has the button
        await message.chat.send_message(  # type: ignore[union-attr]
            " ",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Cancel command
    if text == "/cancel":
        context.user_data[UD_AWAITING_NUMBER] = False  # type: ignore[index]
        await message.reply_text(CANCELLED, reply_markup=MAIN_KEYBOARD)
        return

    # Awaiting track number (explicit mode)
    if context.user_data.get(UD_AWAITING_NUMBER):  # type: ignore[union-attr]
        await _send_track_by_number(message, context, text)
        return

    # Bare number: if a playlist is loaded, treat it as a copy-by-number
    # request even when the user didn't tap the button. This makes the
    # bot more forgiving — they don't have to press "Copy by number"
    # every time.
    if text.isdigit():
        tracks: list[Track] | None = context.user_data.get(UD_TRACKS)  # type: ignore[assignment]
        if tracks:
            await _send_track_by_number(message, context, text)
            return
        await message.reply_text(NUMBER_WITHOUT_CONTEXT, parse_mode=ParseMode.HTML)
        return

    # Otherwise: must be a playlist link
    if "open.spotify.com/playlist/" not in text and "spotify:playlist:" not in text:
        await message.reply_text(NO_LINK_HINT, parse_mode=ParseMode.HTML)
        return

    await _handle_playlist_request(message, settings, text, context)


# ---------------- Application setup ----------------


def build_application(settings: Settings) -> Application:
    """Build and configure the Telegram Application."""
    application = Application.builder().token(settings.bot_token).build()

    application.bot_data["settings"] = settings  # type: ignore[index]

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )
    application.add_handler(CallbackQueryHandler(callback_router))
    return application


async def run_bot(settings: Settings) -> None:
    """Run the bot with long polling, auto-recovering from network blips."""
    application = build_application(settings)
    logger.info("Bot is running...")
    async with application:
        await application.start()
        # Keep polling alive across transient network drops. On platforms
        # like Railway the connection can occasionally hiccup; we restart
        # the updater if it dies.
        backoff = 1.0
        while True:
            try:
                await application.updater.start_polling()  # type: ignore[union-attr]
                backoff = 1.0  # reset on successful start
                # Block until the updater is shut down.
                # The 'running' property is False after shutdown().
                while application.updater.running:  # type: ignore[union-attr]
                    await asyncio.sleep(1.0)
                return  # clean shutdown
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Polling crashed (%s); restarting in %.1fs", exc, backoff
                )
                try:
                    if application.updater.running:  # type: ignore[union-attr]
                        await application.updater.shutdown()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)