# Contributing to m3u Studio

First off — thanks for taking the time to contribute. m3u Studio is a small
local-first tool and any improvement is welcome: bug fixes, features,
translations, better logos, docs, tests, the lot.

## Quick links

- 🐛 Bug report → [open an issue](https://github.com/stepanovandrey89/m3ustudio/issues/new?template=bug_report.yml)
- 💡 Feature idea → [open an issue](https://github.com/stepanovandrey89/m3ustudio/issues/new?template=feature_request.yml)
- 💬 Questions / discussions → [GitHub Discussions](https://github.com/stepanovandrey89/m3ustudio/discussions)

## Development setup

Requirements:

- Python 3.12+
- Node 22+ and `pnpm`
- Optional: `ffmpeg` (for the AC-3 → AAC transcode fallback)

```bash
git clone https://github.com/stepanovandrey89/m3ustudio.git
cd m3ustudio
./run.sh
```

This bootstraps a `.venv`, installs backend + frontend dependencies, then
launches both processes. Ctrl-C stops everything.

Alternatively, use Docker:

```bash
docker compose up --build
```

## Project layout

```
server/        FastAPI backend
  main.py      routes + wiring
  playlist/    m3u parser + serializer
  state/       persisted curated Main state + defaults
  logos/       logo resolvers
  epg/         XMLTV guide
  proxy.py     HLS CORS proxy
  transcode.py ffmpeg manager
web/src/       React 19 + TypeScript + Tailwind v4 frontend
```

Read the "How it works" section of the [README](README.md) before making
architecture changes.

## Code style

### Backend (Python)

- **PEP 8** + type hints on all public functions
- Format & lint with **ruff**:
  ```bash
  ruff format server/
  ruff check server/
  ```
- Prefer immutable dataclasses (`@dataclass(frozen=True, slots=True)`)
- Persist state by name, not by id (ids are derived from URLs and change
  across providers)

### Frontend (TypeScript / React)

- Strict TypeScript, no `any` in application code
- Functional components with hooks
- Tailwind utility classes — avoid hardcoded colors; use CSS variables from
  `src/index.css` so both light and dark themes work
- Format & typecheck:
  ```bash
  cd web
  pnpm tsc --noEmit
  pnpm eslint .
  ```

## Commit messages

Conventional Commits format:

```
<type>: <short summary>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

Examples:

```
feat: add keyboard shortcut for mute
fix: mirror Main reorder into source playlist file
refactor: split SourcePanel into smaller components
docs: add docker compose quickstart
```

## Pull requests

1. Fork the repo and create a topic branch from `main`
2. Make your change with tests where applicable
3. Run lint / typecheck / build locally — CI will run the same checks
4. Open a PR with a clear summary and a test plan
5. Do **not** commit playlist files, stream URLs, tokens, or real logos you
   don't own

## Tips for specific contributions

- **Bug fix**: include a reproduction case in the PR description
- **New feature**: consider opening an issue first to discuss scope
- **UI change**: attach before / after screenshots (and GIFs for interactions)
- **Translation**: currently the UI is English — add a new locale under
  `web/src/i18n/` (if you're the first, propose the structure in an issue)
- **Logo**: drop the PNG into `logos_cache/` locally and send a PR for the
  resolver if you found a new upstream source

## Security

If you discover a security issue, please **do not** open a public issue.
Instead, email the maintainer or use GitHub's private vulnerability reporting.
