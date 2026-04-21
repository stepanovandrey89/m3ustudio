"""Actual OpenAI calls — digest generation and chat streaming.

Kept away from the FastAPI handlers so the business logic stays testable.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from server.ai.client import AIConfig
from server.ai.context import ChannelSchedule, schedule_to_text
from server.ai.digest import Digest, DigestEntry, Theme, digest_from_dict
from server.ai.prompts import chat_system, digest_system
from server.ai.tools import chat_tools

_GROUP_SUFFIX_RE = re.compile(r"\s*\[[^\]]+\]\s*$")
_CHANNEL_ID_RE = re.compile(r"[0-9a-f]{8,}", re.IGNORECASE)
# gpt-5-mini sometimes narrates its function calls as plain text
# ("recommend_programme({...})") in addition to emitting the proper tool_call
# channel event. Strip whole lines that start with one of our tool names and
# an opening paren so the prose stays clean. Applied on streamed text deltas.
_TOOL_CALL_LINE_RE = re.compile(
    r"^[ \t]*(?:recommend_programme|record_programme|list_recordings)\s*\(.*$",
    re.MULTILINE,
)


def _clean_channel_name(name: str) -> str:
    """Strip trailing "[Group]" markers the model occasionally copies verbatim
    from the EPG text header into channel_name. We want the raw channel name,
    not "Матч ТВ HD [Основное]".
    """
    return _GROUP_SUFFIX_RE.sub("", name or "").strip()


def _clean_channel_id(raw: str) -> str:
    """Pull the bare hex out of whatever the model stuck into `channel_id`.

    The EPG header prints `(id=bcd48e4133ca)` and models sometimes copy the
    whole marker verbatim — parens, `id=` prefix and all — which then
    breaks `/api/logo/<id>` on the frontend. Extract the first hex run of
    reasonable length; fall back to the raw string so nothing silently
    drops when the model does the right thing.
    """
    m = _CHANNEL_ID_RE.search(raw or "")
    return m.group(0).lower() if m else (raw or "").strip()


async def generate_digest(
    client: AsyncOpenAI,
    config: AIConfig,
    schedules: list[ChannelSchedule],
    theme: Theme,
    lang: str,
) -> Digest:
    """Ask GPT to pick highlights; return a parsed Digest."""
    epg_text = schedule_to_text(schedules, lang=lang)
    theme_label = {
        "sport": "спорт / sport",
        "cinema": "кино и сериалы / cinema & series",
        "assistant": "рекомендации ассистента / assistant picks",
    }[theme]

    now_iso = datetime.now(UTC).isoformat()
    taste_ru = (
        "ТОЛЬКО ПОЛНОМЕТРАЖНЫЕ ФИЛЬМЫ. Запрещено: сериалы, многосерийные фильмы, "
        "телеспектакли, ток-шоу, документалки-биографии, «истории создания», "
        "рубрики вроде «10 самых», спортивные трансляции.\n"
        "Приоритет: НОВИНКИ (2022+), громкие премьеры 2020-х, культовые хиты "
        "90-х–2000-х (Тарантино, Скорсезе, Бессон, Финчер, Нолан, Джеймс "
        "Кэмерон, «Крёстный отец», «Брат», «Форрест Гамп», «Матрица», «Бойцовский "
        "клуб», «Терминатор»). Любимые всеми кассовые хиты лучше, чем полузабытые "
        "B-movies на СТС-Кино.\n"
        if theme == "cinema"
        else ""
    )
    taste_en = (
        "FEATURE FILMS ONLY. Forbidden: TV series, mini-series, tele-plays, "
        "talk-shows, biographical documentaries, behind-the-scenes shows, "
        'listicle formats ("top 10 …"), sports broadcasts.\n'
        "Prefer: NEW releases (2022+), big 2020s premieres, cult hits from "
        "the 90s–2000s (Tarantino, Scorsese, Besson, Fincher, Nolan, Cameron, "
        "Godfather, Fight Club, Matrix, Terminator). Beloved box-office hits "
        "beat obscure B-movies.\n"
        if theme == "cinema"
        else ""
    )
    count_ru = (
        "Верни 12 уникальных пунктов (разные передачи, без повторов). "
        "Сервер возьмёт из них лучшие 9 после проверки постеров, так что "
        "запас из 12 нужен, чтобы после отсева осталось ровно 9 плиток. "
        "Пустой список только когда в EPG реально ничего по теме нет.\n"
    )
    count_en = (
        "Return 12 unique items (no duplicates). The server picks the top 9 "
        "after poster verification, so the extra 3 are a buffer to guarantee "
        "a full 9-tile board. Empty list only if the EPG genuinely has "
        "nothing on-theme.\n"
    )
    user_prompt = (
        (
            f"Тема: {theme_label}\n"
            f"Сегодня: {date.today().isoformat()} · СЕЙЧАС (UTC): {now_iso}\n"
            "Всё, что ниже — предстоящий эфир (старт минимум через 10 мин, "
            "максимум через 12 часов). Не пиши, что передача «шла» или «прошла».\n"
            f"{count_ru}"
            f"{taste_ru}"
            f"\nEPG избранных каналов:\n{epg_text}\n"
        )
        if lang == "ru"
        else (
            f"Theme: {theme_label}\n"
            f"Today: {date.today().isoformat()} · NOW (UTC): {now_iso}\n"
            "Everything below is upcoming (starts in 10 min to 12 h). "
            "Never say a show was on or aired earlier.\n"
            f"{count_en}"
            f"{taste_en}"
            f"\nFavorite channels EPG:\n{epg_text}\n"
        )
    )

    try:
        response = await client.chat.completions.create(
            model=config.digest_model,
            messages=[
                {"role": "system", "content": digest_system(lang)},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        raise RuntimeError(f"OpenAI call failed: {exc}") from exc

    # Record token usage for the audit endpoint. Swallowed on any
    # failure so the tracker can never break the user-facing path.
    try:
        from server.ai.usage import tracker

        t = tracker()
        if t is not None:
            t.record_from_response(response, operation=f"digest-{theme}", model=config.digest_model)
    except Exception:  # noqa: BLE001
        pass

    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"items": []}

    items = tuple(
        DigestEntry(
            channel_id=_clean_channel_id(str(i.get("channel_id", ""))),
            channel_name=_clean_channel_name(str(i.get("channel_name", ""))),
            title=str(i.get("title", "")),
            start=str(i.get("start", "")),
            stop=str(i.get("stop", "")),
            blurb=str(i.get("blurb", "")),
            poster_keywords=str(i.get("poster_keywords", "")),
        )
        for i in parsed.get("items", [])
        if i.get("title")
    )

    return Digest(
        date=date.today().isoformat(),
        theme=theme,
        lang=lang,
        generated_at=datetime.now(UTC).isoformat(),
        # Keep up to 12 candidates from the model — _hydrate_digest_posters
        # deduplicates + enforces poster presence + slices to the target 9.
        items=items[:12],
    )


async def stream_chat(
    client: AsyncOpenAI,
    config: AIConfig,
    messages: list[dict[str, Any]],
    schedules: list[ChannelSchedule],
    lang: str,
    tool_executor: ToolExecutor,
    deep: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Yield dict events for SSE: {type:'delta'|'tool'|'done'|'error', ...}.

    Supports a single round of function calls. When the model wants to call a
    tool we execute it, append the result, and continue streaming a second
    response that incorporates the tool output.

    ``deep=True`` signals the caller passed a 7-day EPG slice (via the "Хочу
    больше" chip). The system context is adjusted so the model knows it has a
    wider window and must aggressively filter by the user's date/topic query
    instead of offering a generic "what's on tonight" list.
    """
    epg_text = schedule_to_text(schedules, lang=lang, compact=deep)
    now_iso = datetime.now(UTC).isoformat()
    window_ru = "ближайшие 7 суток" if deep else "ближайшие 8 часов"
    window_en = "the next 7 days" if deep else "the next 8 hours"
    deep_hint_ru = (
        "\nПользователь включил глубокий поиск (дата/событие/команда/канал). "
        "Отбирай только то, что реально совпадает с запросом. "
        "ГЛАВНОЕ ПРАВИЛО: как только ты в ответе называешь конкретную передачу "
        "(с названием и временем старта) — ОБЯЗАТЕЛЬНО вызывай для неё "
        "recommend_programme. Цель пользователя — запланировать или записать, "
        "ему нужны карточки с постером и кнопки «Запланировать» / «Записать», "
        "а не только текст. Одно коротко вводное предложение + tool-calls. "
        "Чистый текст допустим только если в EPG ничего не подходит (тогда "
        "одна честная фраза) или вопрос мета («сколько дней EPG загружено»)."
        if deep
        else ""
    )
    deep_hint_en = (
        "\nThe user turned on deep search (date / event / team / channel). "
        "Include only programmes that actually match the query. "
        "KEY RULE: whenever your answer names a concrete programme (title + "
        "start time) you MUST emit recommend_programme for it. The user's "
        "goal is to plan or record — they need poster cards with Plan / "
        "Record buttons, not just prose. One short intro sentence + tool "
        "calls. Plain text is acceptable only when nothing in the EPG "
        "matches (one honest sentence) or the question is meta ('how many "
        "days of EPG are loaded')."
        if deep
        else ""
    )
    # Prompt is split into stable (cached) + volatile (per-call) system
    # messages so OpenAI's prefix-cache can hit across turns within a single
    # chat session. The EPG block is huge but identical between a user's
    # first question and their follow-up; now_iso changes every call and is
    # placed AFTER the cached prefix so it doesn't invalidate it.
    context_msg = {
        "role": "system",
        "content": (
            (
                f"EPG НИЖЕ — только передачи, идущие прямо сейчас или стартующие\n"
                f"в {window_ru}. Всё, что в этом блоке, ЕЩЁ НЕ ЗАКОНЧИЛОСЬ.\n"
                "Формат строк: `ДеньНед ЧЧ:ММ · длительность · Название — описание`\n"
                "Для каждой передачи используй channel_id из `(id=...)` в заголовке."
                f"{deep_hint_ru}\n"
                "=============== EPG START ===============\n"
                f"{epg_text}\n"
                "================ EPG END ================"
            )
            if lang == "ru"
            else (
                f"EPG BELOW contains only programmes airing right now or starting\n"
                f"within {window_en}. Nothing in this block has ended yet.\n"
                "Line format: `WeekDay HH:MM · duration · Title — description`\n"
                "For each programme use the channel_id from `(id=...)` in the header."
                f"{deep_hint_en}\n"
                "=============== EPG START ===============\n"
                f"{epg_text}\n"
                "================ EPG END ================"
            )
        ),
    }
    now_msg = {
        "role": "system",
        "content": f"СЕЙЧАС (UTC): {now_iso}" if lang == "ru" else f"NOW (UTC): {now_iso}",
    }

    full_messages: list[dict[str, Any]] = [
        {"role": "system", "content": chat_system(lang)},
        context_msg,
        now_msg,
        *messages,
    ]
    # Log prompt size so before/after wins are measurable in the journal.
    _prompt_chars = sum(
        len(m.get("content", "")) for m in full_messages if isinstance(m.get("content"), str)
    )
    print(
        f"[ai-chat] prompt size: {_prompt_chars} chars, {len(schedules)} channels, deep={deep}",
        flush=True,
    )

    tools = chat_tools()
    max_rounds = 4

    try:
        for _ in range(max_rounds):
            pending: list[dict[str, Any]] = []
            errored = False
            async for event in _one_round(client, config, full_messages, tools):
                if event["type"] == "tool_call":
                    # Hold these until the stream completes so we can batch
                    # execute every tool call from a single assistant turn
                    # (the model can emit several — e.g. 3 recommendations).
                    pending.append(event)
                    yield event
                else:
                    yield event
                    if event["type"] == "error":
                        errored = True
            if errored or not pending:
                return

            # Record the assistant's tool-calling turn once, then execute
            # every pending tool in PARALLEL — each recommend_programme hits
            # TMDB + Wikipedia so serialising them would stack 3-15s of
            # round-trip per card. Using asyncio.as_completed streams each
            # result to the frontend the moment it resolves; the per-tool
            # order of the final messages list is preserved separately so
            # the next LLM round sees them in the same order the model
            # emitted them.
            full_messages.append(pending[0]["assistant_message"])

            async def _run_call(idx: int, call: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                try:
                    return idx, await tool_executor.execute(call["name"], call["arguments"])
                except Exception as exc:  # noqa: BLE001 — surface to user
                    return idx, {"ok": False, "error": str(exc)}

            tasks = [asyncio.create_task(_run_call(i, c)) for i, c in enumerate(pending)]
            results_ordered: list[dict[str, Any] | None] = [None] * len(pending)
            for fut in asyncio.as_completed(tasks):
                idx, result = await fut
                call = pending[idx]
                results_ordered[idx] = result
                yield {
                    "type": "tool_result",
                    "call_id": call["call_id"],
                    "name": call["name"],
                    "result": result,
                }
            for call, result in zip(pending, results_ordered, strict=True):
                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["call_id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
    except OpenAIError as exc:
        yield {"type": "error", "message": str(exc)}


async def _one_round(
    client: AsyncOpenAI,
    config: AIConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Stream a single OpenAI completion, yielding text deltas and tool calls."""
    stream = await client.chat.completions.create(
        model=config.model,
        messages=messages,
        tools=tools,
        stream=True,
        # Ask OpenAI to include a usage block in the final chunk of a
        # streamed response so the tracker can account for chat tokens
        # the same way as non-streaming calls.
        stream_options={"include_usage": True},
    )

    collected_text: list[str] = []
    # Aggregate streaming tool-call deltas by index.
    tool_calls: dict[int, dict[str, Any]] = {}
    # Line-buffer streamed prose so we can drop whole lines that look like a
    # function-call invocation ("recommend_programme({...})") before they hit
    # the frontend. We emit one complete line at a time, holding the current
    # partial line until a '\n' or end-of-stream.
    line_buffer = ""

    def _drop_tool_call_line(line: str) -> bool:
        return _TOOL_CALL_LINE_RE.match(line.rstrip("\r")) is not None

    async for chunk in stream:
        # With ``stream_options={"include_usage": True}`` OpenAI sends
        # a final usage-only chunk after the content finishes —
        # ``choices`` is empty there. Capture the totals for audit.
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            try:
                from server.ai.usage import tracker

                t = tracker()
                if t is not None:
                    t.record(
                        model=config.model,
                        operation="chat",
                        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                    )
            except Exception:  # noqa: BLE001
                pass
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            collected_text.append(delta.content)
            line_buffer += delta.content
            while "\n" in line_buffer:
                line, _, line_buffer = line_buffer.partition("\n")
                if _drop_tool_call_line(line):
                    continue
                yield {"type": "delta", "text": line + "\n"}
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index or 0
                bucket = tool_calls.setdefault(
                    idx,
                    {"id": "", "name": "", "arguments": ""},
                )
                if tc.id:
                    bucket["id"] = tc.id
                if tc.function and tc.function.name:
                    bucket["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    bucket["arguments"] += tc.function.arguments

    # Flush the final line of prose (no trailing newline) unless it itself is
    # a stray tool-call narration.
    if line_buffer and not _drop_tool_call_line(line_buffer):
        yield {"type": "delta", "text": line_buffer}

    # Emit tool calls after stream completes.
    if tool_calls:
        assistant_message = {
            "role": "assistant",
            "content": "".join(collected_text) or None,
            "tool_calls": [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": b["arguments"]},
                }
                for b in tool_calls.values()
            ],
        }
        for bucket in tool_calls.values():
            try:
                args = json.loads(bucket["arguments"]) if bucket["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            yield {
                "type": "tool_call",
                "call_id": bucket["id"],
                "name": bucket["name"],
                "arguments": args,
                "assistant_message": assistant_message,
            }
    else:
        yield {"type": "done"}


class ToolExecutor:
    """Pluggable tool dispatcher — receives name + args, returns JSON-safe dict."""

    def __init__(
        self,
        *,
        on_record: callable[..., Any] | None = None,
        on_list_recordings: callable[..., Any] | None = None,
        on_recommend: callable[..., Any] | None = None,
    ) -> None:
        self._on_record = on_record
        self._on_list = on_list_recordings
        self._on_recommend = on_recommend

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "record_programme" and self._on_record:
            return await _maybe_await(self._on_record(**args))
        if name == "list_recordings" and self._on_list:
            return await _maybe_await(self._on_list())
        if name == "recommend_programme" and self._on_recommend:
            return await _maybe_await(self._on_recommend(**args))
        return {"ok": False, "error": f"Unknown or unbound tool: {name}"}


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "ToolExecutor",
    "digest_from_dict",
    "generate_digest",
    "stream_chat",
]
