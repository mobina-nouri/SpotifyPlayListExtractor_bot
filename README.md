# 🎵 Spotify Playlist Extractor Bot

A Telegram bot that takes a Spotify playlist link and returns the full list of tracks (song title + artists) as text.

**No Premium account, no Spotify Developer account, no API keys needed.**

---

## ✨ Features

- ✅ Fetches up to ~100 tracks from Spotify's public embed page, with API fallback for larger playlists
- ✅ Each track rendered as a selectable `<code>` block for easy copy
- ✅ Inline **📋 Copy by number** button — tap, send a number, get that single track
- ✅ Persistent **📨 Send new playlist** keyboard button at the bottom of every chat
- ✅ "Fetching..." status message with auto-edit on completion
- ✅ Exponential backoff retry on rate limits
- ✅ Auto-reconnect polling — survives transient network drops
- ✅ Async I/O — never blocks the bot

---

## 🚀 Deploy on Railway

This bot is hosted on [Railway.app](https://railway.app) so anyone can use it. To run your own:

### 1. Create your Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Pick a display name and a username ending in `bot`.
4. Copy the **token** BotFather gives you.

### 2. Deploy

1. Fork this repository.
2. Go to [railway.app](https://railway.app) and sign in with GitHub.
3. Click **New Project → Deploy from GitHub repo**, then select your fork.
4. Railway will detect Python and install dependencies automatically.
5. In the **Variables** tab, add:
   - `BOT_TOKEN` = the token from BotFather
6. Railway will build and start the bot. Watch logs in the **Deployments** tab.

You don't need to set `PORT` — the bot uses long polling, no web port required.

---

## 🛠 Commands

| Command  | Description |
|----------|-------------|
| `/start` | Welcome message + persistent keyboard |
| `/help`  | Usage instructions |

### Buttons
- **📨 Send new playlist** — clears state and prompts for a new link
- **📋 Copy by number** — bot asks for a number, you send one, you get that track's text

---

## 📁 Project structure

```
.
├── bot.py              # Telegram bot handlers and message rendering
├── config.py           # Env loader and validator
├── spotify_client.py   # Spotify embed-page scraper and API fallback
├── main.py             # Entry point
├── test_spotify.py     # Standalone Spotify connection test (developer use)
├── requirements.txt    # Python dependencies
├── Procfile            # Railway worker definition
├── railway.json        # Railway build config
├── runtime.txt         # Python version pin (3.11.9)
├── .env.example        # Sample env file
├── .gitignore
└── README.md
```

---

## ⚠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| `Playlist not found or is private` | Only public playlists are supported. |
| `Could not reach Spotify` | Rate-limited; wait a few minutes and retry. |
| `BOT_TOKEN is not set` | Set the `BOT_TOKEN` variable in Railway's Variables tab. |
| Bot stops responding | Check the **Logs** tab — auto-retry will recover from transient drops. |

---

## 🧪 Developer notes

Run the bot locally for development:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in BOT_TOKEN
python main.py
```

Test the Spotify connection without Telegram:

```bash
python test_spotify.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
```

---

## 📄 License

MIT. Use of the Spotify embed page and API is subject to [Spotify's Terms of Service](https://www.spotify.com/legal/end-user-agreement/).