# r/selfhosted draft

**Title options** (pick one):

1. I vibe-coded an IPTV playlist editor with Claude Code to fix the one thing I hated about m3u8 — channel sorting
2. m3u Studio — self-hosted IPTV playlist editor with drag-and-drop, HLS player, EPG (built with AI)
3. After years of re-sorting a 600-channel m3u8 by hand, I shipped an editor — Claude Code wrote most of it

**Flair**: Release / Self-Promotion (check sub rules)

---

## Body

Hi r/selfhosted 👋

I had a pain I'd been ignoring for years: every IPTV provider ships a 600+ channel m3u8 and **nothing in it is sorted**. Favourites are scattered. `Спорт`, `Кино`, `4K` are dumped in arbitrary order. Renaming a group means hand-editing `#EXTINF` lines. Swap providers — start over.

Last week I sat down with Claude Code and we shipped **m3u Studio** — a local web editor that turned this chore into a two-click thing.

**GitHub**: https://github.com/stepanovandrey89/m3ustudio (MIT)

Most of the code was AI-written. My job was mostly saying "no, that's wrong" and pasting screenshots until it stopped being wrong. ~10k LOC in about two weeks of evenings.

### What it fixes

- **Drag-and-drop channel order** — build a curated "Main" list. Saved by name, not by stream URL, so your order survives provider swaps.
- **Drag-and-drop group order** (`Спорт` ↔ `Кино` ↔ `Новости`) — persistent per-channel, applied to the exported `.m3u8` too.
- **Sort inside groups** — Cyrillic А→Я first, then Latin A→Z (how Russian provider portals display them). Exact same order in the file you export.
- **Main ↔ Source mirror** — edit either side, the other updates. The exported playlist has your `Основное` group in your order.
- **Archive / catch-up with EPG** — click a past programme, it plays from there. Per-channel offset dial for providers whose EPG doesn't align with their actual archive (looking at you, Russian IPTV).
- **Channel logos** — auto-resolved from iptv-org + tv-logos + EPG icons. Logo manager dialog shows status per channel, lets you retry failed, skip stubborn ones, or paste a URL manually.
- **Duplicates detector** — finds near-identical channels across providers so you can drop the redundant ones before exporting.
- **EN / RU UI** with automatic group-name translation on export.

### Stack

- FastAPI + httpx + Pydantic v2
- React 19 + TypeScript + Tailwind v4 + `@dnd-kit` + hls.js + TanStack Query
- Optional: ffmpeg (auto AC-3 → AAC remux for channels the browser refuses to decode)

### Install

```bash
git clone https://github.com/stepanovandrey89/m3ustudio.git
cd m3ustudio
docker compose up -d
```

http://127.0.0.1:8000 — drop your `.m3u8` into `./data/` or upload from the UI.

### Screenshot

![workspace](https://raw.githubusercontent.com/stepanovandrey89/m3ustudio/main/docs/workspace.png)

---

Happy to answer questions about the code, the AI-assisted workflow, or "why not just use Kodi". Feedback welcome — it's still being polished.
