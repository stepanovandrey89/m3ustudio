# Habr / dev.to draft (Russian)

**Заголовок**: Как я написал локальный редактор IPTV-плейлистов на FastAPI + React 19 (и почему всё состояние хранится по именам)

**Хабы**: `React`, `FastAPI`, `TypeScript`, `Tailwind CSS`, `Open source`, `IPTV`, `Python`

**Теги**: `m3u`, `m3u8`, `iptv`, `fastapi`, `react`, `hls`, `epg`, `drag-and-drop`, `self-hosted`

---

## Введение

У меня был плейлист на 600+ IPTV-каналов и стойкая привычка править его
руками в `.m3u8`-файле. Менять порядок каналов, чистить дубликаты,
добавлять любимое в группу «Основное» — всё через Cmd+F и перетаскивание
`#EXTINF`-блоков. Каждый раз при смене провайдера — заново.

В какой-то момент мне это надоело, и я написал **m3u Studio** — локальный
веб-редактор плейлистов с drag-and-drop, встроенным HLS-плеером, EPG и
автоматическим подтягиванием логотипов.

Исходники: https://github.com/stepanovandrey89/m3ustudio (MIT)

![workspace](https://raw.githubusercontent.com/stepanovandrey89/m3ustudio/main/docs/workspace.png)

В этом посте расскажу про интересные архитектурные решения, которые
всплыли по ходу работы.

## Что это вообще такое

Запускаешь `docker compose up -d`, открываешь http://127.0.0.1:8000,
загружаешь свой `.m3u8`. Получаешь двухпанельный интерфейс: слева —
исходный плейлист по группам, справа — твой курируемый список «Main».
Перетаскиваешь каналы между панелями, внутри Main — переупорядочиваешь
drag'n'drop'ом, любой канал кликом открывается в плеере. Экспорт — один
клик: скачивается очищенный `.m3u8` с твоим порядком.

**Стек**: FastAPI + httpx + Pydantic v2 на бэке, React 19 + TypeScript +
Tailwind v4 + `@dnd-kit` + `hls.js` + TanStack Query на фронте. ~10k LOC
суммарно.

## Решение 1: состояние хранится по именам, а не id

Когда парсишь m3u-файл, каждому каналу нужен стабильный идентификатор.
Очевидный выбор — хешировать URL потока:

```python
def _stable_id(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:12]
```

Проблема всплывает, когда пользователь меняет провайдера. URL-ы меняются
полностью — значит, меняются все id-шники, и вся твоя курация («вот мои
любимые 50 каналов в таком-то порядке») идёт к чёрту.

Решение: хранить курируемый порядок по **именам каналов**, а не id.
Имена стабильны между провайдерами. Внутренний store переводит
имя ↔ id на границе через словарь, построенный из текущего плейлиста:

```python
@dataclass(frozen=True, slots=True)
class MainState:
    main_names: tuple[str, ...]

class StateStore:
    def current_ids(self) -> list[str]:
        """Stored names → current playlist ids."""
        with self._lock:
            name_to_id = self._name_to_id_map()
            return [name_to_id[n] for n in self._state.main_names if n in name_to_id]
```

Фронту это незаметно — API отдаёт id-шники, как и раньше. Но загрузишь
новый плейлист от другого провайдера — твоя курация автоматически
перенесётся на новые каналы с теми же именами.

## Решение 2: зеркалирование Main ↔ Source

Курируемый список «Main» и группа «основное» в исходном плейлисте — это,
по сути, одно и то же. Когда пользователь перетаскивает канал в Main, мне
нужно:

1. Обновить `state.json` (курируемый список)
2. Переписать `playlist.m3u8` так, чтобы группа «основное» отражала новый
   порядок (нужно для экспорта и для того, чтобы видеть изменения в левой
   панели)
3. Обновить `default_names.txt` (список имён, который используется как
   «семя» при первом импорте)

Всё это делается одним хелпером `_sync_main_to_source`, который вызывается
из каждого `PATCH /api/main`:

```python
def _sync_main_to_source() -> None:
    main_ids = _state.store.current_ids()

    text = build_with_main_group(
        header=_state.playlist.header,
        all_channels=_state.playlist.channels,
        main_ids=main_ids,
        group_name=MAIN_GROUP_NAME,
    )
    PLAYLIST_PATH.write_text(text, encoding="utf-8")
    _state.playlist = parse_playlist(PLAYLIST_PATH)
    _state.store.bind_playlist(_state.playlist)

    current_names = _state.store.state.main_names
    if current_names:
        DEFAULT_NAMES_PATH.write_text("\n".join(current_names), encoding="utf-8")
        _state.store.set_default_names(current_names)
```

Важный нюанс: я специально **не** вызываю `reload_playlist()` (который
перечитал бы `state.json`), а напрямую ребиндю playlist через
`bind_playlist()`. Иначе получается race condition: drag-and-drop
возвращает старый ответ, потому что `load_or_bootstrap` читает
`state.json`, который ещё не до конца записан.

На фронте React Query инвалидирует кэш источника после каждой мутации:

```ts
onSettled: (server) => {
  if (server) client.setQueryData(KEY_MAIN, server)
  client.invalidateQueries({ queryKey: KEY_SOURCE })
}
```

Результат — обе панели всегда синхронны, без «save»-кнопки.

## Решение 3: HLS-прокси, который переписывает манифесты

IPTV-провайдеры в 90% случаев не отдают CORS-заголовки, поэтому браузер
отказывается проигрывать их потоки напрямую. Классический подход —
сделать прокси, который пропускает запрос через свой сервер.

Тонкость: если просто проксировать master.m3u8, в нём URL-ы на
variant-манифесты, а внутри variant-манифестов — URL-ы на `.ts`-сегменты.
Их все нужно переписать на прокси-URL, иначе плеер запросит сегменты
напрямую и снова упрётся в CORS.

~40 строк Python:

```python
async def proxy_stream(upstream_url: str) -> Response:
    async with httpx.AsyncClient() as client:
        resp = await client.get(upstream_url, follow_redirects=True)
        content_type = resp.headers.get("content-type", "")

        if "mpegurl" in content_type.lower() or upstream_url.endswith(".m3u8"):
            # Rewrite every non-comment line to go through our proxy.
            base = urljoin(upstream_url, ".")
            rewritten = []
            for line in resp.text.splitlines():
                if line.startswith("#") or not line.strip():
                    rewritten.append(line)
                else:
                    absolute = urljoin(base, line)
                    rewritten.append(f"/api/proxy?u={quote(absolute)}")
            return Response("\n".join(rewritten), media_type=content_type)

        return Response(resp.content, media_type=content_type)
```

## Решение 4: AC-3 → AAC на лету

Некоторые провайдеры гонят AC-3 / E-AC-3 аудио, которое Chrome и Safari
упорно отказываются декодировать. Видео играет, звука нет.

Решение — fallback-кнопка «Fix audio», которая на бэке запускает ffmpeg:

```python
proc = await asyncio.create_subprocess_exec(
    FFMPEG_BIN,
    "-i", upstream_url,
    "-c:v", "copy",      # видео не трогаем
    "-c:a", "aac",       # только аудио ремуксируем
    "-f", "hls",
    "-hls_time", "4",
    "-hls_list_size", "6",
    "-hls_flags", "delete_segments",
    str(output_dir / "index.m3u8"),
)
```

Плеер переключается на `/api/transcode/{channel_id}/index.m3u8` и
звук появляется через ~3 секунды (латентность одного HLS-сегмента).
Процессы ffmpeg'а трекаются и убиваются на background-задаче cleanup'а.

## Решение 5: светлая тема поверх dark-only кодовой базы

Фронт изначально писался только под тёмную тему, и в куче мест
захардкожены классы `text-white`, `bg-white/5`, `border-white/10`.
Переписывать тысячи строк на семантические токены — долго.

Пошёл другим путём: добавил в `index.css` переопределения всех этих
utility-классов для `[data-theme="light"]`:

```css
[data-theme="light"] .text-white               { color: var(--color-fog-300); }
[data-theme="light"] .bg-white\/5              { background-color: var(--tint-bg-sm); }
[data-theme="light"] .bg-white\/10             { background-color: var(--tint-bg-md); }
[data-theme="light"] .border-white\/10         { border-color: var(--tint-border-sm); }
[data-theme="light"] .hover\:bg-white\/5:hover { background-color: var(--tint-bg-sm); }
/* … и так далее */
```

Семантические токены `--tint-bg-sm` / `--tint-border-sm` / ... меняются
в зависимости от темы и дают реальную архитектуру elevation'а. Это
работает потому что `[data-theme="light"] .class` имеет specificity
(0,2,0), что перебивает обычный `.class` (0,1,0) из Tailwind.

Не идеально, но работает — и не требует трогать ни одного компонента.

## Что ещё внутри

- Парсер m3u с поддержкой `#EXTGRP`, `tvg-logo`, `tvg-id`, `tvg-rec` (catchup)
- Резолвер логотипов, который идёт по цепочке: локальный override →
  `iptv-org/database` → `tv-logo/tv-logos` CDN → EPG `<icon>`
- Детектор дубликатов каналов на основе нормализованных имён
  (отстригает суффиксы качества вроде `HD`, `FHD`, `UHD`, `+4`)
- XMLTV EPG-загрузчик с кэшированием, день-по-дню раскладкой, jump'ом
  в архив по клику на программу
- Drag-and-drop на `@dnd-kit` с кастомной collision detection'ом,
  который предпочитает строки над контейнером при drag'е из Source в Main
- Встроенный HLS-плеер на `hls.js` с keyboard shortcut'ами, fullscreen'ом,
  архивной перемоткой и записью в MKV

## Как запустить

```bash
git clone https://github.com/stepanovandrey89/m3ustudio.git
cd m3ustudio
docker compose up -d
```

Открываешь http://127.0.0.1:8000, кидаешь свой `.m3u8` через UI,
начинаешь править.

## Что дальше

В TODO:

- Импорт плейлиста по URL (сейчас только file upload)
- Английский дефолтный список каналов как альтернатива русскому
- Экспорт в форматы Kodi / Jellyfin / TiviMate
- Демо-режим на фейковых данных

Код открыт, issues и PR'ы приветствуются. Если какая-то из перечисленных
архитектурных идей показалась интересной — расскажу подробнее в
комментариях.

**GitHub**: https://github.com/stepanovandrey89/m3ustudio

---

## Заметки перед публикацией

- На Habr'е перед публикацией пройдись по хабам — «Open source», «React»,
  «Python», «FastAPI», «IPTV» — должны быть доступны
- Добавь скриншоты (`docs/workspace.png` можно залить прямо в пост)
- Упомяни лицензию (MIT) в самом начале
- Не вставляй упоминания конкретных провайдеров — бан
