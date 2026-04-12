# r/IPTV draft

**Title**: I built a free, self-hosted m3u8 playlist editor with drag-and-drop, EPG, auto logos and an HLS player

**Flair**: Free / Open Source / Tools (check sub rules)

---

## Body

Hi r/IPTV,

I built an open-source tool for curating IPTV playlists and thought some of
you might find it useful: **m3u Studio**.

**GitHub**: https://github.com/stepanovandrey89/m3ustudio
**License**: MIT, completely free, no accounts, nothing phones home.

### What it does

You point it at a `.m3u8` file (or upload one from the UI) and you get a
proper web interface for editing it. No more hand-editing `#EXTINF` lines
in Notepad.

- Drag-and-drop reordering of channels (multi-select supported)
- Live HLS preview with a built-in player (hls.js)
- EPG display via XMLTV — click a programme to jump to its archive position
- Automatic channel logos from iptv-org / tv-logos
- Duplicate detection across providers
- Mirror a curated "Основное" group between the source playlist and your
  favourites list — edit either side, the other follows
- AC-3 → AAC transcode fallback via ffmpeg for channels with broken audio
- One-click export of a cleaned-up playlist or channel name list

### When it's useful

- You have 500+ channels and want to keep only your 50–100 favourites in order
- You switch providers regularly and want your curated ordering to carry
  over automatically (it matches channels by **name**, not by stream URL)
- You want to preview channels before committing to a provider
- You want to strip dead / duplicate channels before loading into Kodi / VLC / TiviMate

### Install (one command)

```bash
docker compose up -d
```

Open http://127.0.0.1:8000. That's it.

There's also a plain `./run.sh` if you don't use Docker.

### Screenshot

![workspace](https://raw.githubusercontent.com/stepanovandrey89/m3ustudio/main/docs/workspace.png)

### Not included

- No provider lists, no account system, no ads — this is just an editor
  for playlists you already have
- No DRM bypass, no anything sketchy — it's a tool for legal streams you
  have access to

Feedback and feature requests welcome. If you hit a bug, open an issue on
GitHub with repro steps (there's a template).

---

**PS**: no affiliation with any provider, I built this for my own setup and
figured I'd share.
