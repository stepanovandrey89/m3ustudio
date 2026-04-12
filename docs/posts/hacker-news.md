# Hacker News — Show HN draft

**Title**: Show HN: m3u Studio — a local IPTV playlist editor (FastAPI + React 19)

> Keep the title ≤80 chars. HN strips emoji. No "I made" — use "Show HN:" prefix.

**URL**: https://github.com/stepanovandrey89/m3ustudio

---

## First comment (always post one on Show HN)

Hi HN — I built m3u Studio because editing IPTV playlists by hand was
eating my Sunday evenings.

The interesting bits, in case you want to poke around the code:

**State by name, not id.** Channel IDs are derived from the stream URL
(hashed), so they're unstable across providers. The curated ordering is
persisted by channel **name** in a small JSON file, so swapping providers
preserves your favourites automatically. v1 → v2 migration on load.

**Main ↔ Source mirroring.** The curated list and the source playlist's
`основное` group are the same thing, just surfaced differently. Every
mutation rewrites the source file through `build_with_main_group` (reorders
the main channels to the top, rewrites `group-title`) and the frontend
invalidates the source cache so the left panel refreshes in lockstep. No
"save" button — autosave on every change.

**HLS proxy that rewrites variant manifests.** Cross-origin streams go
through `/api/proxy?u=<upstream>`, which pipes headers + body *and* rewrites
segment URIs inside the inner manifest so they round-trip through the same
proxy. Otherwise the browser refuses them for CORS. About 40 lines of
Python.

**AC-3 → AAC transcode fallback.** Some providers emit AC-3 / E-AC-3 audio
which neither Chrome nor Safari will decode. When the user clicks "Fix
audio", the backend spawns an ffmpeg process with `-c:v copy -c:a aac`
into a temp HLS directory and the player switches to that stream
transparently. The ffmpeg processes are tracked and cleaned up on a
background task.

**No dark-only assumptions.** The frontend was originally dark-only, then
I bolted on a light theme via CSS variable swaps *and* a utility override
block that remaps every hardcoded `text-white` / `bg-white/5` / `border-white/10`
pattern to dark-tinted equivalents when `data-theme="light"`. Turned out
less painful than doing a full refactor to semantic tokens.

**Stack.** FastAPI + httpx + Pydantic v2 on the backend. React 19 +
TypeScript + Tailwind v4 + `@dnd-kit` + `hls.js` + TanStack Query on the
frontend. ~10k LOC total. Single `docker compose up`.

Happy to answer questions about any of the above — or about why I thought
writing my own IPTV editor was a reasonable use of time (it was).

Repo: https://github.com/stepanovandrey89/m3ustudio

---

## Notes before posting

- Post on a **Tuesday–Thursday, 9–11 AM Pacific** for best visibility
- Don't ask for upvotes. Don't post in r/* subs simultaneously — HN
  detects coordinated promotion
- Reply to every comment in the first 2 hours; the algorithm weighs
  engagement heavily
- If the title gets flagged, just wait — don't repost immediately
