"""FastAPI app entrypoint.

Run locally via:
    uvicorn server.main:app --reload --port 8000

The /api/* endpoints power the React frontend. The frontend is served by
Vite in development and by this app in production (if web/dist exists).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server.epg import EpgGuide
from server.logos import EpgIconIndex, IptvOrgIndex, LogoRegistry, LogoResolver
from server.logos.resolver import _rtrs_candidate
from server.playlist import Channel, Playlist, parse_playlist
from server.playlist.builder import build_playlist, build_with_main_group
from server.proxy import proxy_stream
from server.state import StateStore
from server.state.defaults import DEFAULT_ORDERED_NAMES
from server.transcode import TranscodeManager, run_cleanup_loop

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAYLIST_PATH = Path(os.environ.get("M3U_SOURCE", PROJECT_ROOT / "playlist.m3u8"))
STATE_PATH = Path(os.environ.get("M3U_STATE", PROJECT_ROOT / "state.json"))
DEFAULT_NAMES_PATH = Path(os.environ.get("M3U_DEFAULT_NAMES", PROJECT_ROOT / "default_names.txt"))
LOGO_CACHE = Path(os.environ.get("M3U_LOGO_CACHE", PROJECT_ROOT / "logos_cache"))
EPG_CACHE = Path(os.environ.get("M3U_EPG_CACHE", PROJECT_ROOT / "epg_cache"))
EPG_URL = os.environ.get("M3U_EPG_URL", "http://epg.it999.ru/edem.xml.gz")
TRANSCODE_ROOT = Path(os.environ.get("M3U_TRANSCODE_DIR", PROJECT_ROOT / "transcode_tmp"))
FFMPEG_BIN = os.environ.get("M3U_FFMPEG_BIN", "ffmpeg")
STATIC_DIST = PROJECT_ROOT / "web" / "dist"

MAIN_GROUP_NAME = "Основное"

# Group name translation for export — mirrors web/src/lib/i18n.tsx
_KEEP_AS_IS = {"4K", "UHD", "HD", "FHD", "SD"}
_GROUP_RU_TO_EN: dict[str, str] = {
    "Основное": "Main",
    "Кино": "Movies",
    "Сериалы": "Series",
    "Спорт": "Sport",
    "Новости": "News",
    "Детские": "Kids",
    "Музыка": "Music",
    "Развлекательные": "Entertainment",
    "Познавательные": "Educational",
    "Взрослые": "Adults",
    "Региональные": "Regional",
    "Другие": "Other",
    "Прочие": "Misc",
    "Федеральные": "Federal",
    "Документальные": "Documentary",
}
_GROUP_EN_TO_RU: dict[str, str] = {v: k for k, v in _GROUP_RU_TO_EN.items()}


def _translate_group(name: str, lang: str) -> str:
    if name.upper() in _KEEP_AS_IS:
        return name
    if lang == "en":
        return _GROUP_RU_TO_EN.get(name, name)
    return _GROUP_EN_TO_RU.get(name, name)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class ChannelDTO(BaseModel):
    id: str
    name: str
    url: str
    group: str
    tvg_id: str = ""
    has_logo: bool = False
    catchup_days: int = 0


class SourceResponse(BaseModel):
    total: int
    groups: dict[str, list[ChannelDTO]]


class MainResponse(BaseModel):
    ids: list[str]
    channels: list[ChannelDTO]


class ReorderBody(BaseModel):
    op: Literal["reorder"]
    ids: list[str]


class AddBody(BaseModel):
    op: Literal["add"]
    id: str
    position: int | None = None


class RemoveBody(BaseModel):
    op: Literal["remove"]
    id: str


class MoveBody(BaseModel):
    op: Literal["move"]
    id: str
    to: int = Field(..., ge=0)


MainOperation = ReorderBody | AddBody | RemoveBody | MoveBody


class RenameGroupBody(BaseModel):
    op: Literal["rename_group"]
    old: str
    new: str


class DeleteChannelBody(BaseModel):
    op: Literal["delete_channel"]
    id: str


class MoveChannelBody(BaseModel):
    op: Literal["move_channel"]
    id: str
    group: str


SourceOperation = RenameGroupBody | DeleteChannelBody | MoveChannelBody


# ---------------------------------------------------------------------------
# Default channel names helpers
# ---------------------------------------------------------------------------


def _load_default_names() -> tuple[str, ...]:
    """Return custom default channel names if saved, otherwise the built-in list."""
    if DEFAULT_NAMES_PATH.exists():
        try:
            text = DEFAULT_NAMES_PATH.read_text(encoding="utf-8")
            names = tuple(line.strip() for line in text.splitlines() if line.strip())
            if names:
                return names
        except OSError:
            pass
    return DEFAULT_ORDERED_NAMES


# ---------------------------------------------------------------------------
# App state (reloaded on each request via Depends would be cleaner, but this
# is a single-user local app so module-level is fine)
# ---------------------------------------------------------------------------


class AppState:
    def __init__(self) -> None:
        self.playlist: Playlist = Playlist(header=(), channels=())
        self.store: StateStore = StateStore(STATE_PATH, default_names=_load_default_names())
        self.iptv_index: IptvOrgIndex = IptvOrgIndex(LOGO_CACHE)
        self.epg_icons: EpgIconIndex = EpgIconIndex()
        self.logos: LogoResolver = LogoResolver(
            LOGO_CACHE,
            iptv_index=self.iptv_index,
            epg_index=self.epg_icons,
        )
        self.epg: EpgGuide = EpgGuide(EPG_CACHE, url=EPG_URL)
        self.logo_registry: LogoRegistry = LogoRegistry(LOGO_CACHE)
        self.transcode: TranscodeManager = TranscodeManager(TRANSCODE_ROOT, ffmpeg_bin=FFMPEG_BIN)
        self.transcode_cleanup_task: asyncio.Task | None = None

    def reload_playlist(self) -> None:
        self.playlist = parse_playlist(PLAYLIST_PATH)
        self.store.load_or_bootstrap(self.playlist)
        # Extend the original-groups snapshot with any channels not seen
        # before. Non-destructive: existing entries are preserved so
        # "group rewritten by previous sync" data never clobbers history.
        self.store.capture_original_groups(self.playlist)


_state = AppState()
_warming_done: bool = False


def _channel_to_dto(ch: Channel) -> ChannelDTO:
    """Build a ChannelDTO.

    `has_logo` is True only when the logo is already on disk. This guarantees
    that every /api/logo/<id> request returns 200 — no 404s ever reach the UI.
    Logos are pre-warmed in the background at startup.
    """
    return ChannelDTO(
        id=ch.id,
        name=ch.name,
        url=ch.url,
        group=ch.group or "без группы",
        tvg_id=ch.tvg_id,
        has_logo=_state.logos.has_cached(ch.name),
        catchup_days=ch.catchup_days,
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(title="m3u Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    # Fresh install / after clear: create an empty valid playlist so the
    # server can boot and the user can import one from the UI.
    if not PLAYLIST_PATH.exists():
        PLAYLIST_PATH.write_text("#EXTM3U\n", encoding="utf-8")
    _state.reload_playlist()
    # iptv-org index download is best-effort and doesn't block startup on failure.
    with contextlib.suppress(Exception):  # deliberately swallow network errors
        await _state.iptv_index.load()
    # EPG icon index — parse from cached XML if available (fast, no network)
    epg_gz = EPG_CACHE / "_epg.xml.gz"
    _state.epg_icons.load_from_xml_gz(epg_gz)
    # EPG is large (~43MB + parse) — fire it off in the background so the
    # first /api/source request isn't blocked. Subsequent /api/epg/* calls
    # return empty until the guide finishes loading.
    asyncio.create_task(_load_epg_in_background())
    asyncio.create_task(_warm_logo_cache())
    # Background task that kills idle transcode sessions every 30s.
    _state.transcode_cleanup_task = asyncio.create_task(run_cleanup_loop(_state.transcode))


async def _load_epg_in_background() -> None:
    # Network / parse errors must not crash the app — fail silently.
    with contextlib.suppress(Exception):
        await _state.epg.load()


async def _warm_logo_cache() -> None:
    """Download logos for all resolvable channels that aren't yet cached.

    Runs once per startup. Up to 8 channels are downloaded concurrently.
    Sets _warming_done = True when finished so the frontend can refetch.
    """
    global _warming_done
    reg = _state.logo_registry
    # Give the app a moment to finish startup before hammering the network.
    await asyncio.sleep(2)

    # Build / refresh registry entries for every channel. Populate EPG URLs.
    for ch in _state.playlist.channels:
        epg_url = _state.epg_icons.lookup(ch.name) or ""
        reg.ensure_channel(ch.id, ch.name, epg_url)

    # Sync cached flags with what's actually on disk (e.g. manual drops).
    reg.update_cached_flags(_state.logos.has_cached)
    reg.save()

    # Candidates: channels that need resolution and haven't exhausted retries.
    candidates = [
        ch
        for ch in _state.playlist.channels
        if not _state.logos.has_cached(ch.name) and reg.should_retry(ch.id)
    ]

    if not candidates:
        _warming_done = True
        return

    sem = asyncio.Semaphore(8)

    async def _fetch_one(ch: Channel) -> None:
        async with sem:
            try:
                result = await _state.logos.resolve(ch.name)
                if result:
                    # Determine which source succeeded
                    source = ""
                    if _rtrs_candidate(ch.name):
                        source = "rtrs"
                    elif _state.iptv_index.lookup(ch.name):
                        source = "iptv-org"
                    elif _state.epg_icons.lookup(ch.name):
                        source = "epg"
                    else:
                        source = "cdn"
                    reg.mark_found(ch.id, source)
                else:
                    reg.mark_miss(ch.id)
            except Exception:  # noqa: BLE001
                reg.mark_miss(ch.id)

    await asyncio.gather(*[_fetch_one(ch) for ch in candidates])
    _warming_done = True


@app.on_event("shutdown")
async def _shutdown() -> None:
    # Kill all ffmpeg children and clean up temp dirs.
    if _state.transcode_cleanup_task is not None:
        _state.transcode_cleanup_task.cancel()
    await _state.transcode.stop_all()


# ---------- Playlist endpoints ----------


@app.get("/api/source", response_model=SourceResponse)
def get_source() -> SourceResponse:
    grouped: dict[str, list[ChannelDTO]] = {}
    for ch in _state.playlist.channels:
        dto = _channel_to_dto(ch)
        grouped.setdefault(dto.group, []).append(dto)
    # Apply stored group order (groups not in the order list go last, alphabetically)
    order = _state.store.get_group_order()
    if order:
        order_map = {name: i for i, name in enumerate(order)}
        fallback = len(order)
        sorted_groups: dict[str, list[ChannelDTO]] = {}
        for name in sorted(grouped, key=lambda g: (order_map.get(g, fallback), g.lower())):
            sorted_groups[name] = grouped[name]
        grouped = sorted_groups
    return SourceResponse(total=len(_state.playlist.channels), groups=grouped)


@app.get("/api/main", response_model=MainResponse)
def get_main() -> MainResponse:
    ids = _state.store.current_ids()
    by_id = {ch.id: ch for ch in _state.playlist.channels}
    channels = [_channel_to_dto(by_id[cid]) for cid in ids if cid in by_id]
    return MainResponse(ids=ids, channels=channels)


@app.patch("/api/source", response_model=SourceResponse)
def patch_source(body: SourceOperation) -> SourceResponse:
    if body.op == "rename_group":
        new_channels = tuple(
            ch.with_group(body.new) if ch.group == body.old else ch
            for ch in _state.playlist.channels
        )
        new_playlist = Playlist(header=_state.playlist.header, channels=new_channels)
        text = build_playlist(new_playlist.header, new_playlist.channels)
        PLAYLIST_PATH.write_text(text, encoding="utf-8")
        _state.reload_playlist()

    elif body.op == "delete_channel":
        # Remove from main list before losing the id→name mapping
        _state.store.remove_id(body.id)
        new_channels = tuple(ch for ch in _state.playlist.channels if ch.id != body.id)
        new_playlist = Playlist(header=_state.playlist.header, channels=new_channels)
        text = build_playlist(new_playlist.header, new_playlist.channels)
        PLAYLIST_PATH.write_text(text, encoding="utf-8")
        _state.reload_playlist()

    elif body.op == "move_channel":
        new_channels = tuple(
            ch.with_group(body.group) if ch.id == body.id else ch for ch in _state.playlist.channels
        )
        new_playlist = Playlist(header=_state.playlist.header, channels=new_channels)
        text = build_playlist(new_playlist.header, new_playlist.channels)
        PLAYLIST_PATH.write_text(text, encoding="utf-8")
        _state.reload_playlist()

    return get_source()


def _sync_main_to_source() -> None:
    """Mirror Main state into two places:

    1. The source playlist file — rewrite PLAYLIST_PATH so the left panel's
       'основное' group reflects the same channels in the same order.
    2. The stored default channel names — Main is the authoritative curated
       list, so changes to it become the new bootstrap defaults.

    The in-memory playlist is updated by re-parsing the freshly written
    file and rebinding the store's playlist reference — we intentionally
    skip `load_or_bootstrap` because state.json is already authoritative
    from the caller's mutation.
    """
    main_ids = _state.store.current_ids()

    # Rewrite the source file with Main at the top, tagged as "основное".
    # Pass the original-groups snapshot so that channels removed from Main
    # are restored to their real groups instead of leaking as ghost entries
    # still tagged "основное".
    text = build_with_main_group(
        header=_state.playlist.header,
        all_channels=_state.playlist.channels,
        main_ids=main_ids,
        group_name=MAIN_GROUP_NAME,
        original_groups=_state.store.original_groups_map(),
        group_order=_state.store.get_group_order(),
    )
    PLAYLIST_PATH.write_text(text, encoding="utf-8")
    _state.playlist = parse_playlist(PLAYLIST_PATH)
    _state.store.bind_playlist(_state.playlist)

    # Persist the new ordering as the default channel name list so it
    # survives future clears / fresh imports.
    current_names = _state.store.state.main_names
    if current_names:
        DEFAULT_NAMES_PATH.write_text("\n".join(current_names), encoding="utf-8")
        _state.store.set_default_names(current_names)


@app.patch("/api/main", response_model=MainResponse)
def patch_main(body: MainOperation) -> MainResponse:
    if body.op == "reorder":
        _state.store.replace_ids(body.ids)
    elif body.op == "add":
        if body.id not in {ch.id for ch in _state.playlist.channels}:
            raise HTTPException(404, f"Unknown channel id: {body.id}")
        _state.store.add_id(body.id, body.position)
    elif body.op == "remove":
        _state.store.remove_id(body.id)
    elif body.op == "move":
        _state.store.move_id(body.id, body.to)

    # Mirror Main ordering/membership into the source's 'основное' group.
    _sync_main_to_source()
    return get_main()


@app.get("/api/export/names.txt")
def export_names() -> PlainTextResponse:
    """Download a plain-text list of channel names in the main playlist."""
    ids = _state.store.current_ids()
    by_id = {ch.id: ch for ch in _state.playlist.channels}
    names = [by_id[cid].name for cid in ids if cid in by_id]
    return PlainTextResponse(
        content="\n".join(names),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="main_channels.txt"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/export.m3u8")
def export_playlist(lang: str = Query(default="ru")) -> PlainTextResponse:
    main_group = _translate_group(MAIN_GROUP_NAME, lang)
    channels = _state.playlist.channels
    # Translate group names when exporting in a different language.
    if lang != "ru":
        channels = tuple(ch.with_group(_translate_group(ch.group, lang)) for ch in channels)
    text = build_with_main_group(
        header=_state.playlist.header,
        all_channels=channels,
        main_ids=_state.store.current_ids(),
        group_name=main_group,
        original_groups=_state.store.original_groups_map(),
        group_order=_state.store.get_group_order(),
    )
    return PlainTextResponse(
        content=text,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Content-Disposition": 'attachment; filename="playlist_main.m3u8"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/reload")
def reload_playlist() -> JSONResponse:
    _state.reload_playlist()
    return JSONResponse({"ok": True, "total": len(_state.playlist.channels)})


def _match_names_to_ids(names_text: str, channels: tuple[Channel, ...]) -> list[str]:
    """Match newline-separated channel names to channel ids.

    First tries exact case-insensitive match, then falls back to substring.
    Order of the input list is preserved in the result.
    """
    lines = [line.strip() for line in names_text.splitlines() if line.strip()]
    name_to_id: dict[str, str] = {}
    for ch in channels:
        key = ch.name.lower()
        if key not in name_to_id:
            name_to_id[key] = ch.id

    result: list[str] = []
    seen: set[str] = set()

    for wanted in lines:
        wanted_lower = wanted.lower()
        # 1. Exact match
        if wanted_lower in name_to_id:
            cid = name_to_id[wanted_lower]
            if cid not in seen:
                result.append(cid)
                seen.add(cid)
            continue
        # 2. Substring fallback
        for ch in channels:
            ch_lower = ch.name.lower()
            if wanted_lower in ch_lower or ch_lower in wanted_lower:
                if ch.id not in seen:
                    result.append(ch.id)
                    seen.add(ch.id)
                break

    return result


@app.post("/api/import")
async def import_playlist(
    file: UploadFile = File(...),  # noqa: B008 — idiomatic FastAPI dependency
    names: str | None = Form(default=None),  # noqa: B008
) -> JSONResponse:
    """Replace the source playlist with an uploaded .m3u8 file.

    Population rules (applied in order):
    1. If `names` is explicitly provided → use those channel names for Main.
    2. If the imported playlist already contains a "основное" group → seed
       Main from that group. If that group has MORE channels than the stored
       static defaults, promote it to become the new defaults.
    3. Otherwise → match the stored static defaults against the imported
       channels by name, create the "основное" group in the source file
       itself, and seed Main from those matches.
    """
    content = await file.read()
    PLAYLIST_PATH.write_bytes(content)

    # Clean slate — we will seed Main explicitly below or via bootstrap.
    STATE_PATH.unlink(missing_ok=True)

    _state.reload_playlist()
    # New provider → old id/group snapshot is obsolete. Replace it with a
    # fresh one BEFORE the first rewrite, so we still have the real pre-sync
    # groups on record. After this point, the ghost-fix in
    # build_with_main_group will have accurate data to work with.
    _state.store.reset_original_groups(_state.playlist)

    # Case 1: explicit user-provided channel list takes priority.
    if names and names.strip():
        matched = _match_names_to_ids(names, _state.playlist.channels)
        _state.store.replace_ids(matched)
        return JSONResponse({"ok": True, "total": len(_state.playlist.channels)})

    # Case 2: source already has a "основное" group — carry it over.
    existing_main = [
        ch for ch in _state.playlist.channels if ch.group.lower() == MAIN_GROUP_NAME.lower()
    ]

    if existing_main:
        _state.store.replace_ids([ch.id for ch in existing_main])

        # Promote to new defaults if this curated list is larger than the
        # stored defaults — keeps the bootstrap list growing with the user's
        # latest curation.
        source_main_names = tuple(ch.name for ch in existing_main)
        if len(source_main_names) > len(_load_default_names()):
            DEFAULT_NAMES_PATH.write_text("\n".join(source_main_names), encoding="utf-8")
            _state.store.set_default_names(source_main_names)

        return JSONResponse({"ok": True, "total": len(_state.playlist.channels)})

    # Case 3: no "основное" group in the source. load_or_bootstrap already
    # seeded Main state by matching the stored defaults against channels in
    # the imported playlist (by name). Now physically create the group in
    # the source file so it persists there as well.
    seeded_ids = _state.store.current_ids()
    if seeded_ids:
        new_text = build_with_main_group(
            header=_state.playlist.header,
            all_channels=_state.playlist.channels,
            main_ids=seeded_ids,
            group_name=MAIN_GROUP_NAME,
            original_groups=_state.store.original_groups_map(),
            group_order=_state.store.get_group_order(),
        )
        PLAYLIST_PATH.write_text(new_text, encoding="utf-8")
        # Reload so in-memory channels reflect the new group-title.
        # capture_original_groups() inside reload_playlist is non-destructive,
        # so the real original groups we captured above survive intact.
        _state.reload_playlist()

    return JSONResponse({"ok": True, "total": len(_state.playlist.channels)})


@app.post("/api/state/clear")
def clear_main_state() -> JSONResponse:
    """Clear both the source playlist and the main list, resetting to a blank state."""
    # Overwrite source with an empty playlist so the left panel is also blank
    PLAYLIST_PATH.write_text("#EXTM3U\n", encoding="utf-8")
    # Delete the state file so the next import triggers a fresh bootstrap
    STATE_PATH.unlink(missing_ok=True)
    # Reload so in-memory state matches the now-empty file
    _state.reload_playlist()
    return JSONResponse({"ok": True})


# ---------- Group order ----------


class GroupOrderBody(BaseModel):
    order: list[str] = Field(default_factory=list)


@app.get("/api/groups/order")
def get_group_order() -> JSONResponse:
    return JSONResponse({"order": _state.store.get_group_order()})


@app.put("/api/groups/order")
def put_group_order(body: GroupOrderBody) -> JSONResponse:
    _state.store.set_group_order(body.order)
    return JSONResponse({"ok": True})


# ---------- Default channel names ----------


class DefaultNamesBody(BaseModel):
    names: list[str] = Field(default_factory=list)


@app.get("/api/defaults/names")
def get_default_names() -> JSONResponse:
    """Return the stored default channel names (used to bootstrap Main on first import)."""
    names = list(_load_default_names())
    return JSONResponse({"names": names})


@app.put("/api/defaults/names")
def put_default_names(body: DefaultNamesBody) -> JSONResponse:
    """Save a new set of default channel names and update the in-memory defaults."""
    names = tuple(n.strip() for n in body.names if n.strip())
    DEFAULT_NAMES_PATH.write_text("\n".join(names), encoding="utf-8")
    _state.store.set_default_names(names)
    return JSONResponse({"ok": True, "count": len(names)})


# ---------- Duplicates ----------

_DUP_QUALITY_RE = re.compile(r"\s*(hd|fhd|uhd|4k|8k|sd|hq|\+\d*)\s*$", re.IGNORECASE)


def _dup_key(name: str) -> str:
    """Normalise channel name for duplicate detection (strips quality suffixes)."""
    s = _DUP_QUALITY_RE.sub("", name.strip().lower())
    return re.sub(r"\s+", " ", s).strip()


@app.get("/api/duplicates")
def get_duplicates() -> JSONResponse:
    """Return groups of channels that are likely duplicates.

    Two passes:
    1. Group by tvg_id (non-empty) — same EPG binding → same channel.
    2. Group by normalised name (strips HD/FHD/UHD/4K) — same show, different quality.
    Each channel appears in at most one group.
    """
    channels = list(_state.playlist.channels)
    groups: list[dict] = []
    used_ids: set[str] = set()

    # Pass 1 — same tvg_id
    tvg_map: dict[str, list] = {}
    for ch in channels:
        if ch.tvg_id:
            tvg_map.setdefault(ch.tvg_id, []).append(ch)
    for key, chs in tvg_map.items():
        if len(chs) > 1 and not {c.id for c in chs}.issubset(used_ids):
            groups.append(
                {
                    "key": key,
                    "reason": "tvg_id",
                    "channels": [_channel_to_dto(c).model_dump() for c in chs],
                }
            )
            used_ids.update(c.id for c in chs)

    # Pass 2 — similar name (after quality-suffix removal)
    name_map: dict[str, list] = {}
    for ch in channels:
        if ch.id not in used_ids:
            k = _dup_key(ch.name)
            if k:
                name_map.setdefault(k, []).append(ch)
    for key, chs in name_map.items():
        if len(chs) > 1:
            groups.append(
                {
                    "key": key,
                    "reason": "name",
                    "channels": [_channel_to_dto(c).model_dump() for c in chs],
                }
            )
            used_ids.update(c.id for c in chs)

    return JSONResponse({"total": len(groups), "groups": groups})


# ---------- Logo & proxy endpoints ----------


@app.get("/api/logos/status")
def logos_status() -> JSONResponse:
    """Return whether the background logo warming task has finished."""
    return JSONResponse(
        {
            "warmed": _warming_done,
            **_state.logo_registry.stats(),
        }
    )


@app.get("/api/logos/registry")
def get_logo_registry(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    q: str = Query(default=""),
    status: str = Query(default=""),
) -> JSONResponse:
    """Paginated, searchable logo registry."""
    from dataclasses import asdict

    entries = _state.logo_registry.all_entries()
    items = [{"id": cid, **asdict(entry)} for cid, entry in entries.items()]

    # Filter by status
    if status:
        items = [i for i in items if i["status"] == status]

    # Search by channel name
    if q:
        q_lower = q.lower()
        items = [i for i in items if q_lower in i["name"].lower()]

    # Sort: pending first, then missing, then found; alphabetically within.
    status_order = {"pending": 0, "missing": 1, "found": 2}
    items.sort(key=lambda i: (status_order.get(i["status"], 9), i["name"].lower()))

    total = len(items)
    start = (page - 1) * per_page
    page_items = items[start : start + per_page]

    return JSONResponse(
        {
            "items": page_items,
            "total": total,
            "page": page,
            "pages": max(1, (total + per_page - 1) // per_page),
            **_state.logo_registry.stats(),
        }
    )


@app.post("/api/logos/retry/{channel_id}")
async def retry_logo(channel_id: str) -> JSONResponse:
    """Reset and retry logo resolution for a single channel."""
    ch = _state.playlist.by_id(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    _state.logo_registry.reset_for_retry(channel_id)
    # Clear from resolver's miss cache so it tries again.
    _state.logos.clear_miss(ch.name)
    result = await _state.logos.resolve(ch.name)
    if result:
        _state.logo_registry.mark_found(channel_id, "retry")
    else:
        _state.logo_registry.mark_miss(channel_id)
    return JSONResponse({"ok": True, "found": result is not None})


@app.post("/api/logos/retry-all")
async def retry_all_logos() -> JSONResponse:
    """Reset all failed logos and re-run warming."""
    count = _state.logo_registry.reset_all_failed()
    if count > 0:
        global _warming_done
        _warming_done = False
        asyncio.create_task(_warm_logo_cache())
    return JSONResponse({"ok": True, "reset": count})


@app.post("/api/logos/skip/{channel_id}")
def skip_logo(channel_id: str) -> JSONResponse:
    """Mark a channel logo as skipped — no more retries."""
    _state.logo_registry.mark_skipped(channel_id)
    return JSONResponse({"ok": True})


@app.post("/api/logos/override/{channel_id}")
async def override_logo(
    channel_id: str,
    url: str = Query(..., description="URL of the logo image"),
) -> JSONResponse:
    """Manually set a logo from a URL for a channel."""
    import httpx

    ch = _state.playlist.by_id(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "m3u-studio/1.0"})
            resp.raise_for_status()
            data = resp.content
            if not data:
                raise HTTPException(400, "Empty response from URL")
            _state.logos.save_to_cache(ch.name, data)
            _state.logo_registry.mark_manual(channel_id, ch.name)
            return JSONResponse({"ok": True})
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Failed to fetch logo: {exc}") from exc


@app.get("/api/logo/{channel_id}")
async def get_logo(channel_id: str) -> Response:
    channel = _state.playlist.by_id(channel_id)
    if channel is None:
        raise HTTPException(404, "Unknown channel")

    data = await _state.logos.resolve(channel.name)
    if data is None:
        raise HTTPException(404, "No logo")

    # Detect actual format — some sources return SVG despite our .png extension
    media_type = "image/svg+xml" if data[:5].lstrip().startswith(b"<") else "image/png"

    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/epg/{channel_id}")
def get_epg(channel_id: str, past: int = 24, future: int = 24) -> JSONResponse:
    """Return programmes around 'now' for the given channel.

    `past` and `future` are window sizes in hours (defaults to ±24h = full
    day of history + full day of upcoming). The response carries the ordered
    programme list plus `current_index` pointing at whichever programme is
    airing right now (or null if there is no match).
    """
    from datetime import timedelta

    channel = _state.playlist.by_id(channel_id)
    if channel is None:
        raise HTTPException(404, "Unknown channel")

    current_index, programmes = _state.epg.window(
        channel.name,
        past=timedelta(hours=max(0, past)),
        future=timedelta(hours=max(0, future)),
    )
    return JSONResponse(
        {
            "loaded": _state.epg.loaded,
            "loading": _state.epg.loading,
            "catchup_days": channel.catchup_days,
            "current_index": current_index,
            "programmes": [p.to_dict() for p in programmes],
        }
    )


@app.get("/api/proxy")
async def proxy(u: str = Query(...)) -> Response:
    return await proxy_stream(u, proxy_base="/api/proxy")


# ---------- Transcode (AC-3 → AAC) ----------


@app.post("/api/transcode/{channel_id}/start")
async def transcode_start(channel_id: str) -> JSONResponse:
    channel = _state.playlist.by_id(channel_id)
    if channel is None:
        raise HTTPException(404, "Unknown channel")
    try:
        session = await _state.transcode.ensure_started(channel_id, channel.url)
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    return JSONResponse(
        {
            "ok": True,
            "manifest_url": f"/api/transcode/{channel_id}/index.m3u8",
            "started_at": session.started_at,
        }
    )


@app.delete("/api/transcode/{channel_id}")
async def transcode_stop(channel_id: str) -> JSONResponse:
    killed = await _state.transcode.stop(channel_id)
    return JSONResponse({"stopped": killed})


@app.get("/api/transcode/{channel_id}/{filename}")
def transcode_file(channel_id: str, filename: str) -> Response:
    # Keep the session alive while a client is pulling segments from it.
    _state.transcode.touch(channel_id)

    session = _state.transcode.get(channel_id)
    if session is None:
        raise HTTPException(404, "Transcode session not running")

    # Guard against path traversal — only simple filenames allowed.
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")

    path = session.session_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")

    # Determine media type by extension — FastAPI's FileResponse guesses
    # application/octet-stream for .m3u8 otherwise.
    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
        headers = {"Cache-Control": "no-store"}
    elif filename.endswith(".ts"):
        media_type = "video/mp2t"
        headers = {"Cache-Control": "public, max-age=30"}
    else:
        media_type = "application/octet-stream"
        headers = {}

    return FileResponse(path, media_type=media_type, headers=headers)


# ---------- Static frontend (production build) ----------


if STATIC_DIST.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIST / "index.html")
