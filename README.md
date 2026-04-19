# m3u Studio

> Local web studio for curating, previewing, and exporting m3u/m3u8 IPTV playlists.

[![CI](https://github.com/stepanovandrey89/m3ustudio/actions/workflows/ci.yml/badge.svg)](https://github.com/stepanovandrey89/m3ustudio/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind-4-38BDF8?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](./Dockerfile)
[![Self-Hosted](https://img.shields.io/badge/self--hosted-ready-%2344cc11)](https://github.com/awesome-selfhosted/awesome-selfhosted)

![m3u Studio workspace](docs/workspace.png)

A self-hosted playlist editor with drag-and-drop reordering, live HLS preview,
integrated EPG, automatic logo resolution, duplicate detection, and one-click
export to a cleaned-up `.m3u8` file.

Now with an **AI concierge** that plans your viewing, a **daily digest** of
on-theme picks with real movie posters, a **watch-later dashboard** that
pings a **Telegram bot** when your programme is about to start, and a local
**MKV archive** for your recordings.

Built as a small, fast, local-first tool: one `docker compose up` (or
`./run.sh`) and you have a FastAPI backend + React 19 frontend running on
your machine.

---

## Features

**Editing**
- Drag-and-drop channels between the source panel and the curated main list
- Multi-select with hold-to-select, bulk remove, bulk reorder
- Rename groups, move channels between groups, delete channels
- Inline search with substring + tvg-id matching
- Autosave on every change — no save button

**Playback**
- Built-in HLS player (`hls.js`) with archive / catchup support
- Keyboard shortcuts: `←/→` prev/next channel, `Space` play/pause, `F` fullscreen, `G` toggle EPG
- Optional AC-3 → AAC transcode fallback via ffmpeg for channels whose audio Safari/Chrome refuse to decode
- Now-playing overlay driven by EPG data
- Record-to-MKV from the player

**EPG**
- Downloads and caches an XMLTV guide (default: `epg.it999.ru/edem.xml.gz`)
- Shows a scrollable programme list per channel with Today / Yesterday / Tomorrow day headers
- Click any programme to jump to its archive position

**Logos**
- Automatic resolution from `iptv-org/database`, `tv-logo/tv-logos`, and EPG icons
- On-demand background warming at startup, cached to `logos_cache/`
- Drop-in override: any PNG named after a channel slug takes precedence

**Organisation**
- Mirrored "Main" group between the source file and the curated main list — edit either side and the other follows
- Configurable default channel order (editable via Settings) used as the bootstrap seed for fresh imports
- Duplicate detection that groups near-identical channel names across providers
- Import / export: upload a new `.m3u8`, download the curated one, export just the channel names as `.txt`

**AI concierge** *(optional — needs an OpenAI key)*
- Streaming chat powered by `gpt-5-mini` that knows your favourite channels'
  EPG for the next 12 hours
- Recommendations come as rich poster cards (not plain text) via the
  `recommend_programme` tool call — every pick renders with channel logo,
  time, live countdown, and action buttons
- One-click actions per card: **Plan to watch** (adds to dashboard + Telegram)
  and **Record** (spawns background ffmpeg → archive)
- Strict future-tense only: the EPG context window filters out anything
  airing now or in the past, and the prompt forbids past-tense phrasing

**Daily Digest**
- Bento-grid of curated picks for the day across three themes: **Sport**,
  **Cinema**, **Assistant**
- Each card shows a real poster pulled from **TMDB** (optional API key) or
  Wikipedia fallback, with blurred-logo backdrop when nothing matches
- Cached per day — generation runs only when you hit Refresh
- Live countdown stays in sync with the clock (tick every 30 s)

**Plans (watch-later)**
- Dashboard of scheduled broadcasts sorted by start time
- Status chips: Scheduled · Live now · Done · Cancelled · Missed
- **Telegram bot integration** — on "Plan to watch" the bot posts a compact
  poster card to your chat; one minute before the show starts it posts a
  second alert with a "🔴 Watch now" deep-link straight back into the
  player (auto-play with `?watch=<channel_id>`)
- Deleting a plan in the UI also deletes the Telegram cards (within 48 h,
  per Telegram API limits)
- All plans persist to `plans.json` — survive restarts

**Archive (recordings)**
- Thematic grid of your MKV recordings (Sport / Cinema / Assistant) with
  poster backdrops
- Inline `<video>` playback for finished recordings
- Download, cancel in-progress, or delete; status reflects in real time
- Recordings started from the AI assistant land under the **Assistant**
  bucket automatically

**UI**
- Top-level section navigation: **Playlist · Assistant · Today · Plans · Archive**
- Dark and light themes, toggleable from the header
- Responsive: desktop two-panel layout with `@dnd-kit`, mobile tab bar
- Glass morphism on dark, opaque cream-white panels on light
- Decorative animated background, mouse-follow glow, grid overlay

---

## Quick start

### 🐳 Docker (recommended)

```bash
git clone https://github.com/stepanovandrey89/m3ustudio.git
cd m3ustudio
docker compose up -d
```

Open http://127.0.0.1:8000 — that's it. Your playlist, state and all
caches are persisted under `./data/` next to the compose file.

Drop your `.m3u8` file into `./data/playlist.m3u8` (or upload it from the
UI via Settings → Import playlist) and start editing.

Stop with `docker compose down`.

### 💻 Local dev

Requires Python 3.12+, `pnpm`, and (optionally) `ffmpeg` for audio transcode fallback.

```bash
./run.sh
```

This bootstraps a `.venv`, installs backend + frontend deps, and launches
both processes. When it's ready, open:

- Frontend → http://127.0.0.1:5173 (or `http://<your-LAN-ip>:5173`)
- API → http://127.0.0.1:8000

Stop everything with `Ctrl-C`.

### First-time setup

1. Place your `.m3u8` playlist at `./playlist.m3u8` **or** use **Settings → Import playlist** from the UI.
2. On first run, "Main" is seeded from `default_names.txt` (or `server/state/defaults.py` if absent) — those channel names are matched against the imported playlist to form the initial curated list.
3. Edit from there: drag, drop, rename, remove. Every change is autosaved to `state.json` and mirrored back into `playlist.m3u8` under the `основное` group.

---

## How it works

### Data model

- **Source playlist** — `playlist.m3u8` — the raw provider file, parsed into an
  immutable in-memory `Playlist` model. Edits like rename / move / delete are
  round-tripped by rewriting the file byte-for-byte through `build_playlist`.

- **Main state** — `state.json` — the curated ordering, persisted by channel
  **name** (not id). Names are stable across provider swaps; id hashes are not.
  Stored under `main_names` as a v2 JSON schema, with v1 → v2 migration on load.

- **Default channel order** — `default_names.txt` — the bootstrap seed used
  when the state file is absent. Updates automatically whenever you reorder
  Main, so it always reflects your latest curation.

### Import pipeline

When you upload a new playlist via `POST /api/import`, the server applies
priority rules:

1. If an explicit newline-separated **channel name list** is provided, match
   and seed Main from those names.
2. Else if the imported playlist already contains a `group-title="основное"`,
   seed Main from that group. If it's larger than the stored defaults, promote
   it to become the new defaults.
3. Else bootstrap Main from the stored default names and physically inject
   the `основное` group into the imported playlist via `build_with_main_group`.

### Main ↔ Source mirroring

Every mutation to Main (reorder, add, remove, move) runs `_sync_main_to_source`:

1. Rewrites `playlist.m3u8` with Main channels tagged as `основное` and
   placed at the top, followed by the rest of the playlist untouched.
2. Re-parses the file in-memory and rebinds the store's playlist reference.
3. Saves the new name order to `default_names.txt` and updates the in-memory
   defaults so they survive future clears and fresh imports.

The frontend invalidates the source React Query cache on every Main mutation,
so the left "Основное" group visually refreshes in lockstep.

### HLS proxy

Cross-origin streams go through `GET /api/proxy?u=<upstream>` which pipes
headers + body through and also rewrites the inner variant manifests so their
segment URIs round-trip through the same proxy. This sidesteps browser CORS
restrictions for playlists whose providers don't send `Access-Control-Allow-*`.

### Transcode fallback

Some providers emit AC-3 / E-AC-3 audio which the browser refuses to decode.
`POST /api/transcode/{channel_id}/start` spawns an `ffmpeg` process that
remuxes the stream with `-c:v copy -c:a aac` into a temp HLS directory served
back over `GET /api/transcode/{channel_id}/{file}`. The player switches to this
stream transparently when the user clicks the "Fix audio" button.

### Logo resolution

On startup, `LogoResolver` warms a background task that goes through every
channel and tries, in order:

1. Manual override in `logos_cache/` matching the channel slug
2. `iptv-org/database` index lookup by name / tvg-id
3. `tv-logo/tv-logos` CDN candidate URLs (`_rtrs_candidate`)
4. EPG XMLTV `<icon>` tag

First hit wins, result is cached to `logos_cache/` and served via
`GET /api/logo/{channel_id}`.

---

## API surface

| Method | Path                                  | Purpose                               |
|--------|---------------------------------------|---------------------------------------|
| GET    | `/api/source`                         | Grouped source channels               |
| PATCH  | `/api/source`                         | Rename group / move / delete channel  |
| GET    | `/api/main`                           | Curated main list                     |
| PATCH  | `/api/main`                           | reorder / add / remove / move         |
| POST   | `/api/import`                         | Replace source from uploaded .m3u8    |
| POST   | `/api/state/clear`                    | Wipe both source and main             |
| GET    | `/api/defaults/names`                 | Stored default channel order          |
| PUT    | `/api/defaults/names`                 | Save new default channel order        |
| GET    | `/api/export.m3u8`                    | Download curated playlist             |
| GET    | `/api/export/names.txt`               | Download channel name list            |
| GET    | `/api/duplicates`                     | Detected duplicate groups             |
| GET    | `/api/epg/{channel_id}`               | EPG programmes for a channel          |
| GET    | `/api/logo/{channel_id}`              | Resolved channel logo                 |
| GET    | `/api/proxy?u=<upstream>`             | CORS-safe HLS proxy                   |
| POST   | `/api/transcode/{channel_id}/start`   | Start ffmpeg AC-3 → AAC               |
| DELETE | `/api/transcode/{channel_id}`         | Stop transcode                        |
| GET    | `/api/ai/status`                      | OpenAI client enabled + model name    |
| POST   | `/api/ai/chat`                        | Streaming assistant chat (SSE)        |
| GET    | `/api/ai/digest?theme=…&lang=…`       | 10-pick daily digest (cached)         |
| DELETE | `/api/ai/digest`                      | Wipe all cached digests               |
| GET    | `/api/ai/poster?keywords=…`           | Resolve poster (TMDB → Wikipedia)     |
| GET    | `/api/plans`                          | List scheduled plans                  |
| POST   | `/api/plans`                          | Create plan + Telegram alert          |
| POST   | `/api/plans/{id}/cancel`              | Mark plan cancelled                   |
| DELETE | `/api/plans/{id}`                     | Delete plan + Telegram cards          |
| GET    | `/api/plans/status`                   | Telegram config health                |
| POST   | `/api/plans/test`                     | Send a test message to the bot chat   |
| GET    | `/api/recordings`                     | List MKV recordings                   |
| POST   | `/api/recordings`                     | Queue a recording                     |
| POST   | `/api/recordings/{id}/cancel`         | Cancel in-progress recording          |
| DELETE | `/api/recordings/{id}`                | Delete recording + file               |
| GET    | `/api/recordings/{id}/file`           | Download / stream MKV                 |

---

## Configuration

Environment variables. Core ones are optional — safe defaults apply if unset.
The AI / Telegram block is optional — the app runs fine without any of them,
just with the corresponding features dimmed in the UI.

**Core**

| Variable               | Default                     | Purpose                              |
|------------------------|-----------------------------|--------------------------------------|
| `M3U_SOURCE`           | `./playlist.m3u8`           | Source playlist path                 |
| `M3U_STATE`            | `./state.json`              | Main state file                      |
| `M3U_DEFAULT_NAMES`    | `./default_names.txt`       | Default channel order                |
| `M3U_LOGO_CACHE`       | `./logos_cache`             | Logo cache directory                 |
| `M3U_EPG_CACHE`        | `./epg_cache`               | EPG cache directory                  |
| `M3U_EPG_URL`          | `http://epg.it999.ru/edem.xml.gz` | XMLTV guide URL                |
| `M3U_TRANSCODE_DIR`    | `./transcode_tmp`           | Temp dir for ffmpeg HLS output       |
| `M3U_FFMPEG_BIN`       | `ffmpeg`                    | ffmpeg binary path                   |
| `M3U_AI_CACHE`         | `./ai_cache`                | Digest + poster cache                |
| `M3U_RECORDINGS`       | `./recordings`              | MKV recordings directory             |
| `M3U_PLANS`            | `./plans.json`              | Scheduled plans file                 |

**AI Assistant + Daily Digest** *(optional)*

| Variable           | Purpose                                                     |
|--------------------|-------------------------------------------------------------|
| `OPENAI_API_KEY`   | Enables the assistant and digest. Get one at [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `OPENAI_MODEL`     | Defaults to `gpt-5-mini`. `gpt-5-nano` is cheaper if you're price-sensitive |
| `TMDB_API_KEY`     | Optional, for proper movie/series posters. Free key from [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) (use the short "API Key v3") |

**Telegram bot** *(optional, powers Plans notifications)*

| Variable             | Purpose                                                    |
|----------------------|------------------------------------------------------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from `@BotFather`                                |
| `TELEGRAM_CHAT_ID`   | Chat or channel id the bot posts to                        |
| `PUBLIC_BASE_URL`    | Base URL Telegram clients use for "Watch now" deep-links (e.g. `https://your-domain` or `http://<LAN-ip>:8000`) |

Upload `bot_logo.png` from the repo root as the bot's avatar via
`@BotFather` → `/setuserpic`.

A template is provided in [`.env.example`](./.env.example) — copy to `.env`
and fill in what you want enabled.

---

## Project layout

```
.
├── server/                     FastAPI backend
│   ├── main.py                 routes + wiring
│   ├── ai_api.py               /api/ai/*, /api/plans/*, /api/recordings/*
│   ├── ai/                     OpenAI client, EPG context, prompts, digest, poster resolver
│   ├── planner/                PlanStore + background Telegram scheduler
│   ├── notify/                 Telegram Bot API client
│   ├── recordings/             ffmpeg recording manager (MKV output)
│   ├── playlist/               m3u parser + serializer
│   ├── state/                  persisted Main state + defaults
│   ├── logos/                  logo resolvers (iptv-org, tv-logos, EPG icons)
│   ├── epg/                    XMLTV guide loader
│   ├── proxy.py                HLS CORS proxy
│   └── transcode.py            ffmpeg AC-3 → AAC manager
├── web/
│   └── src/
│       ├── App.tsx             DnD context + layout + section routing
│       ├── components/         SourcePanel, MainPanel, PlayerModal, EpgPanel,
│       │                       AIAssistant, DailyDigest, PlansPanel, ArchivePanel, SectionNav, …
│       ├── hooks/              usePlaylist, useTheme, useIsMobile, usePoster, useNow
│       ├── lib/                api client, cn, archive helpers, SSE reader
│       └── index.css           dark/light theme tokens + utility overrides
├── default_names.txt           bootstrap channel order (mutable)
├── bot_logo.png                Telegram bot avatar (512×512)
├── .env.example                env template (OpenAI / TMDB / Telegram)
├── run.sh                      one-shot dev launcher
└── pyproject.toml              backend deps
```

---

## Stack

- **Backend** — FastAPI, Uvicorn, httpx, Pydantic v2, python-multipart, OpenAI SDK, python-dotenv, Pillow
- **Frontend** — React 19, TypeScript 5, Vite, Tailwind v4, `@dnd-kit/core` + `/sortable`, Framer Motion, `hls.js`, `@tanstack/react-query`, Lucide icons
- **External APIs** *(optional)* — OpenAI (`gpt-5-mini`), TMDB v3, Wikipedia REST, Telegram Bot API
- **Tooling** — Ruff, pytest, ESLint, pnpm

---

## Support the project

If m3u Studio saves you time, you can send any amount as a tip:

**USDT (TRC20)** — `TLB4mTGtmUrbEvKN78kg4b1AdCD4Jxnf1k`

Every contribution helps keep the project maintained and improved.

---

## License

MIT — see [LICENSE](LICENSE).
