# r/selfhosted draft

**Title options** (pick one):

1. I built a self-hosted IPTV m3u8 playlist editor with drag-and-drop, HLS player and EPG
2. m3u Studio — a local web UI for editing your IPTV playlist (FastAPI + React, Docker)
3. Tired of editing m3u8 files by hand — so I made a drag-and-drop editor with live HLS preview

**Flair**: Release / Self-Promotion (check sub rules)

---

## Body

Hey r/selfhosted 👋

I wrote a small self-hosted tool for people who curate their own IPTV
playlists: **m3u Studio**. It's a local web UI that parses your `.m3u8` file,
lets you drag-and-drop channels into a curated "main" list, preview anything
live in a built-in HLS player, and export a cleaned-up playlist.

**GitHub**: https://github.com/stepanovandrey89/m3ustudio

### Why

I was tired of doing this in a text editor:

- Reordering 90+ favourite channels by moving `#EXTINF` blocks around
- Previewing streams to check they still work
- Keeping logos and EPG in sync across providers
- Repeating the whole curation when I switched providers

So I built a tool that treats the playlist as a database and my curated
ordering as state, independent of the source file.

### Features

- **Drag-and-drop** editor with multi-select, group rename, delete, cross-panel drag
- **Built-in HLS player** (hls.js) with archive/catchup, keyboard shortcuts, fullscreen, record-to-MKV
- **EPG integration** — loads an XMLTV guide, shows programmes with Today / Yesterday / Tomorrow headers, click to jump to archive position
- **Automatic logo resolution** from iptv-org/database + tv-logo/tv-logos + EPG `<icon>` tags, cached locally
- **Mirrored "Main" group** — the curated list is kept in sync on both sides: edit in the UI, and the source playlist file's `основное` group gets rewritten to match
- **AC-3 → AAC transcode fallback** via ffmpeg for channels whose audio the browser refuses to decode
- **Duplicate detection** across providers (groups near-identical names)
- **Import / export** — upload new source, download curated playlist or channel name list
- **Dark and light themes**, responsive mobile layout

### Stack

- Backend: FastAPI, httpx, Pydantic v2
- Frontend: React 19, TypeScript, Tailwind v4, `@dnd-kit`, Framer Motion, hls.js, TanStack Query
- Optional: ffmpeg for transcode

### Install

```bash
git clone https://github.com/stepanovandrey89/m3ustudio.git
cd m3ustudio
docker compose up -d
```

http://127.0.0.1:8000 — drop your playlist into `./data/playlist.m3u8` or
upload from the UI.

### Screenshot

![workspace](https://raw.githubusercontent.com/stepanovandrey89/m3ustudio/main/docs/workspace.png)

---

Open to feedback, feature ideas, or "you should have used X instead" —
it's MIT licensed and the codebase is small enough to hack on.
