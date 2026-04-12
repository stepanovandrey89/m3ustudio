# Changelog

All notable changes to m3u Studio will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Docker + `docker-compose.yml` for one-command deployment
- GitHub Actions CI: ruff, typecheck, frontend build, docker image build
- Issue templates (bug, feature) and pull request template
- `CONTRIBUTING.md` with setup + style guide
- `CHANGELOG.md`

## [0.1.0] - 2026-04-12

### Added
- Initial release of m3u Studio
- **Editing** — drag-and-drop channel editing, multi-select, group rename,
  channel move between groups, channel delete, autosave
- **Playback** — built-in HLS player with archive/catchup, keyboard shortcuts,
  now-playing overlay, fullscreen, record-to-MKV
- **EPG** — XMLTV guide loader with day headers and archive jump
- **Logos** — automatic resolution from iptv-org, tv-logos, EPG icons; drop-in
  overrides via `logos_cache/`
- **Main / Source mirroring** — every Main mutation rewrites the source
  playlist's `основное` group and updates `default_names.txt`
- **Import / export** — upload new `.m3u8`, download curated playlist or
  channel name list
- **Duplicate detection** — groups near-identical channels across providers
- **Transcode fallback** — ffmpeg AC-3 → AAC remux for channels the browser
  can't decode natively
- **Themes** — dark and light, toggleable from the header
- **Responsive UI** — desktop two-panel layout with `@dnd-kit`, mobile tab bar

[Unreleased]: https://github.com/stepanovandrey89/m3ustudio/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/stepanovandrey89/m3ustudio/releases/tag/v0.1.0
