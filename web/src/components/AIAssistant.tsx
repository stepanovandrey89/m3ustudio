import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowUp,
  BookmarkCheck,
  CalendarSearch,
  Check,
  Clock,
  CircleStop,
  Circle,
  Eraser,
  Film,
  Loader2,
  Sparkles,
  Trophy,
  Video,
  Wand2,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useI18n } from '../lib/i18n'
import { useNow, formatCountdown } from '../hooks/useNow'
import { cn } from '../lib/cn'
import { sseStream } from '../lib/sse'

type ChatTurn =
  | { id: string; role: 'user'; text: string }
  | {
      id: string
      role: 'assistant'
      text: string
      tools: ToolEvent[]
      streaming: boolean
    }

interface ToolEvent {
  call_id: string
  name: string
  args: Record<string, unknown>
  result?: Record<string, unknown>
}

interface RecommendedProgramme {
  channel_id: string
  title: string
  start: string
  stop: string
  poster_keywords?: string
  blurb?: string
  theme?: string
}

interface AIAssistantProps {
  enabled: boolean
  loadingStatus: boolean
  onPlan?: (entry: RecommendedProgramme) => void | Promise<void>
  onRecord?: (entry: RecommendedProgramme) => void | Promise<void>
}

// v2: ToolEvent gained `call_id` for proper tool_result matching.
const STORAGE_KEY = 'm3u_ai_chat_v2'
const DEEP_MODE_KEY = 'm3u_ai_deep_mode'

export function AIAssistant({ enabled, loadingStatus, onPlan, onRecord }: AIAssistantProps) {
  const { t, lang } = useI18n()
  const [turns, setTurns] = useState<ChatTurn[]>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      return raw ? (JSON.parse(raw) as ChatTurn[]) : []
    } catch {
      return []
    }
  })
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  // When true, user messages are sent with deep_search:true so the backend
  // expands the EPG window to 7 days. Activated by the "Хочу больше" chip
  // and persisted in localStorage so a page reload doesn't silently drop it
  // while the clarification turn is still visible in the chat history.
  const [deepMode, setDeepMode] = useState<boolean>(() => {
    try {
      return localStorage.getItem(DEEP_MODE_KEY) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      if (deepMode) localStorage.setItem(DEEP_MODE_KEY, '1')
      else localStorage.removeItem(DEEP_MODE_KEY)
    } catch {
      /* */
    }
  }, [deepMode])
  const abortRef = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(turns.slice(-40)))
    } catch {
      /* quota */
    }
  }, [turns])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || streaming || !enabled) return
      const userTurn: ChatTurn = {
        id: `u-${Date.now()}`,
        role: 'user',
        text: trimmed,
      }
      const assistantTurn: ChatTurn = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        text: '',
        tools: [],
        streaming: true,
      }
      setTurns((t) => [...t, userTurn, assistantTurn])
      setInput('')
      setStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller

      const messages = [...turns, userTurn].map((t) =>
        t.role === 'user'
          ? { role: 'user', content: t.text }
          : { role: 'assistant', content: t.text },
      )

      // Deep mode stays armed for the whole thread — user turns it off via
      // the × badge or by clearing the chat.
      try {
        for await (const evt of sseStream<StreamEvent>('/api/ai/chat', {
          signal: controller.signal,
          body: { messages, lang, deep_search: deepMode },
        })) {
          setTurns((all) =>
            all.map((t) =>
              t.id === assistantTurn.id && t.role === 'assistant'
                ? applyEvent(t, evt)
                : t,
            ),
          )
          if (evt.type === 'done' || evt.type === 'error') {
            setTurns((all) =>
              all.map((t) =>
                t.id === assistantTurn.id && t.role === 'assistant'
                  ? { ...t, streaming: false }
                  : t,
              ),
            )
          }
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setTurns((all) =>
            all.map((t) =>
              t.id === assistantTurn.id && t.role === 'assistant'
                ? {
                    ...t,
                    text:
                      t.text +
                      `\n\n⚠ ${err instanceof Error ? err.message : String(err)}`,
                    streaming: false,
                  }
                : t,
            ),
          )
        }
      } finally {
        setStreaming(false)
        abortRef.current = null
        setTurns((all) =>
          all.map((t) =>
            t.role === 'assistant' && t.id === assistantTurn.id
              ? { ...t, streaming: false }
              : t,
          ),
        )
      }
    },
    [deepMode, enabled, lang, streaming, turns],
  )

  // Handler for the "Хочу больше" chip — inject an assistant clarification
  // message, arm deep mode, and hand focus back to the input so the user can
  // type their specific question.
  const askForDeepClarification = useCallback(() => {
    if (streaming) return
    const prompt: ChatTurn = {
      id: `a-deep-${Date.now()}`,
      role: 'assistant',
      text: t('ai_deep_prompt'),
      tools: [],
      streaming: false,
    }
    setTurns((prev) => [...prev, prompt])
    setDeepMode(true)
    setTimeout(() => inputRef.current?.focus(), 50)
  }, [streaming, t])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const clear = useCallback(() => {
    if (streaming) stop()
    setTurns([])
    setDeepMode(false)
    try {
      localStorage.removeItem(STORAGE_KEY)
      localStorage.removeItem(DEEP_MODE_KEY)
    } catch {
      /* */
    }
  }, [streaming, stop])

  const suggestions: {
    icon: typeof Trophy
    key: string
    mode?: 'send' | 'deep'
  }[] = [
    { icon: Trophy, key: 'ai_suggest_sport', mode: 'send' },
    { icon: Film, key: 'ai_suggest_cinema', mode: 'send' },
    { icon: Wand2, key: 'ai_suggest_random', mode: 'send' },
    { icon: CalendarSearch, key: 'ai_suggest_deep', mode: 'deep' },
  ]

  if (!enabled && !loadingStatus) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-1 items-center justify-center px-6 py-12">
        <div className="glass rounded-3xl border-white/10 p-8 text-center">
          <Sparkles className="mx-auto mb-3 h-8 w-8 text-[var(--color-indigo-primary)]" />
          <p className="text-sm text-fog-200/80">{t('ai_disabled')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="relative mx-auto flex min-h-full w-full max-w-3xl flex-1 flex-col px-4 pb-4 pt-6 sm:px-6">
      {/* Header strip */}
      <div className="flex items-end justify-between gap-3 pb-4">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.28em] text-fog-200/50">
            <Sparkles className="h-3 w-3" /> {t('section_ai')}
          </div>
          <h1 className="mt-1 text-[clamp(1.6rem,1.2rem+1.4vw,2.4rem)] font-semibold leading-[1.05] tracking-tight text-white">
            {t('ai_title')}
          </h1>
          <p className="mt-1 max-w-md text-[13px] text-fog-200/60">
            {t('ai_subtitle')}
          </p>
        </div>
        {turns.length > 0 && (
          <button
            type="button"
            onClick={clear}
            className="glass flex items-center gap-1.5 rounded-full border-white/10 px-3 py-1.5 text-[11px] text-fog-200/70 hover:text-white"
          >
            <Eraser className="h-3 w-3" />
            {t('ai_clear')}
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="glass flex-1 rounded-3xl border-white/10 px-4 py-5 sm:px-6 sm:py-6">
        {turns.length === 0 ? (
          <div className="flex flex-col items-start gap-6 py-6">
            <div className="max-w-lg space-y-2">
              <div className="text-[clamp(1.4rem,1rem+1.2vw,1.9rem)] font-semibold leading-[1.1] tracking-tight text-white">
                {lang === 'ru'
                  ? 'Что сегодня интересного?'
                  : 'What\'s good tonight?'}
              </div>
              <p className="text-[13px] text-fog-200/70">
                {lang === 'ru'
                  ? 'Задайте вопрос или нажмите одну из подсказок.'
                  : 'Ask a question or tap a suggestion below.'}
              </p>
            </div>
            <div className="grid w-full gap-2 sm:grid-cols-2">
              {suggestions.map((s) => {
                const Icon = s.icon
                const isDeep = s.mode === 'deep'
                return (
                  <button
                    key={s.key}
                    type="button"
                    onClick={() =>
                      isDeep ? askForDeepClarification() : send(t(s.key))
                    }
                    className={cn(
                      'group relative flex items-center gap-3 overflow-hidden rounded-2xl border px-4 py-3 text-left transition',
                      isDeep
                        ? 'border-[var(--color-amber-primary)]/30 bg-[var(--color-amber-primary)]/[0.06] hover:border-[var(--color-amber-primary)]/60 hover:bg-[var(--color-amber-primary)]/[0.1]'
                        : 'border-white/10 bg-white/[0.03] hover:border-[var(--color-indigo-primary)]/40 hover:bg-white/[0.06]',
                    )}
                  >
                    <Icon
                      className={cn(
                        'h-4 w-4 shrink-0',
                        isDeep
                          ? 'text-[var(--color-amber-primary)]'
                          : 'text-[var(--color-indigo-primary)]',
                      )}
                    />
                    <span className="text-[13px] text-fog-100">
                      {t(s.key)}
                    </span>
                    <ArrowUp className="ml-auto h-3.5 w-3.5 shrink-0 rotate-45 opacity-0 transition group-hover:opacity-60" />
                  </button>
                )
              })}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <AnimatePresence initial={false}>
              {turns.map((turn) =>
                turn.role === 'user' ? (
                  <UserBubble key={turn.id} text={turn.text} />
                ) : (
                  <AssistantBubble
                    key={turn.id}
                    turn={turn}
                    onPlan={onPlan}
                    onRecord={onRecord}
                  />
                ),
              )}
            </AnimatePresence>
            <div ref={bottomRef} aria-hidden className="h-0" />
          </div>
        )}
      </div>

      {/* Composer */}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void send(input)
        }}
        className={cn(
          'glass-strong sticky bottom-2 z-10 mt-3 flex items-end gap-2 rounded-3xl border px-3 py-2.5 shadow-[0_12px_32px_-16px_rgba(0,0,0,0.6)] transition',
          deepMode
            ? 'border-[var(--color-amber-primary)]/50'
            : 'border-white/10',
        )}
      >
        {deepMode && (
          <div className="pointer-events-none absolute -top-3 left-4 flex items-center gap-1.5 rounded-full border border-[var(--color-amber-primary)]/40 bg-black/80 px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.2em] text-[var(--color-amber-primary)] backdrop-blur">
            <CalendarSearch className="h-3 w-3" />
            {t('ai_deep_badge')}
            <button
              type="button"
              onClick={() => setDeepMode(false)}
              className="pointer-events-auto -mr-1 ml-1 text-[var(--color-amber-primary)]/70 transition hover:text-[var(--color-amber-primary)]"
              aria-label="cancel deep mode"
            >
              ×
            </button>
          </div>
        )}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void send(input)
            }
          }}
          placeholder={deepMode ? t('ai_deep_placeholder') : t('ai_placeholder')}
          rows={1}
          disabled={!enabled}
          className="min-h-10 flex-1 resize-none bg-transparent px-3 py-2 text-[14px] text-fog-100 outline-none placeholder:text-fog-200/40 disabled:opacity-50"
          style={{ maxHeight: 160 }}
        />
        {streaming ? (
          <button
            type="button"
            onClick={stop}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-white transition hover:bg-white/20"
            aria-label={t('ai_stop')}
          >
            <CircleStop className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!enabled || !input.trim()}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-[var(--color-indigo-primary)] to-[var(--color-cyan-primary)] text-white shadow-[0_6px_18px_-8px_rgba(212,165,86,0.8)] transition hover:brightness-110 disabled:opacity-30 disabled:shadow-none"
            aria-label={t('ai_send')}
          >
            <ArrowUp className="h-4 w-4" strokeWidth={2.4} />
          </button>
        )}
      </form>
    </div>
  )
}

type StreamEvent =
  | { type: 'delta'; text: string }
  | {
      type: 'tool_call'
      name: string
      call_id: string
      arguments: Record<string, unknown>
    }
  | {
      type: 'tool_result'
      call_id: string
      name: string
      result: Record<string, unknown>
    }
  | { type: 'done' }
  | { type: 'error'; message: string }

/**
 * Sort recommend_programme tool cards by their `start` time ascending so the
 * user always sees the nearest broadcast first. Non-recommend tools (e.g.
 * record_programme confirmations) keep their original order and fall to the
 * end of the sorted list.
 */
function sortToolsByStart(tools: ToolEvent[]): ToolEvent[] {
  return [...tools].sort((a, b) => {
    const aStart = String(a.args?.start ?? (a.result as { start?: string })?.start ?? '')
    const bStart = String(b.args?.start ?? (b.result as { start?: string })?.start ?? '')
    if (!aStart && !bStart) return 0
    if (!aStart) return 1
    if (!bStart) return -1
    return aStart < bStart ? -1 : aStart > bStart ? 1 : 0
  })
}

function applyEvent(
  turn: Extract<ChatTurn, { role: 'assistant' }>,
  event: StreamEvent,
): ChatTurn {
  if (event.type === 'delta') {
    return { ...turn, text: turn.text + event.text }
  }
  if (event.type === 'tool_call') {
    // Dedupe: if we've already seen this call_id, merge instead of duplicating.
    const existingIdx = turn.tools.findIndex((t) => t.call_id === event.call_id)
    if (existingIdx !== -1) {
      const next = [...turn.tools]
      next[existingIdx] = {
        ...next[existingIdx],
        name: event.name,
        args: event.arguments,
      }
      return { ...turn, tools: next }
    }
    return {
      ...turn,
      tools: [
        ...turn.tools,
        { call_id: event.call_id, name: event.name, args: event.arguments },
      ],
    }
  }
  if (event.type === 'tool_result') {
    // Match strictly by call_id — without this, multiple calls of the same
    // tool (e.g. 5x recommend_programme) all overwrite the last card.
    const idx = turn.tools.findIndex((t) => t.call_id === event.call_id)
    if (idx !== -1) {
      const next = [...turn.tools]
      next[idx] = { ...next[idx], result: event.result }
      return { ...turn, tools: next }
    }
    return {
      ...turn,
      tools: [
        ...turn.tools,
        {
          call_id: event.call_id,
          name: event.name,
          args: {},
          result: event.result,
        },
      ],
    }
  }
  if (event.type === 'error') {
    return { ...turn, text: turn.text + `\n\n⚠ ${event.message}` }
  }
  return turn
}

function UserBubble({ text }: { text: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="ml-auto max-w-[75%]"
    >
      <div className="rounded-2xl rounded-tr-md border border-[var(--color-indigo-primary)]/30 bg-[var(--color-indigo-primary)]/[0.12] px-4 py-2.5 text-[14px] leading-relaxed text-white">
        {text}
      </div>
    </motion.div>
  )
}

interface AssistantBubbleProps {
  turn: Extract<ChatTurn, { role: 'assistant' }>
  onPlan?: (entry: RecommendedProgramme) => void | Promise<void>
  onRecord?: (entry: RecommendedProgramme) => void | Promise<void>
}

function AssistantBubble({ turn, onPlan, onRecord }: AssistantBubbleProps) {
  const { t } = useI18n()
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="mr-auto max-w-[85%]"
    >
      <div className="flex items-start gap-3">
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[var(--color-indigo-primary)] to-[var(--color-cyan-primary)]">
          <Sparkles className="h-3.5 w-3.5 text-white" />
        </div>
        <div className="flex flex-col gap-2">
          {turn.text && (
            <div className="whitespace-pre-wrap text-[14px] leading-[1.6] text-fog-100">
              {turn.text}
              {turn.streaming && (
                <motion.span
                  animate={{ opacity: [0.2, 1, 0.2] }}
                  transition={{ duration: 1.2, repeat: Infinity }}
                  className="ml-0.5 inline-block h-3 w-1 align-middle bg-[var(--color-indigo-primary)]"
                />
              )}
            </div>
          )}
          {sortToolsByStart(turn.tools).map((tool, i) => (
            <ToolCard
              key={tool.call_id || `tool-${i}`}
              tool={tool}
              onPlan={onPlan}
              onRecord={onRecord}
            />
          ))}
          {turn.streaming && !turn.text && (
            <div className="flex items-center gap-2 text-[12px] text-fog-200/60">
              <Circle className="h-2 w-2 animate-pulse fill-current" />
              {t('ai_thinking')}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}

interface ToolCardProps {
  tool: ToolEvent
  onPlan?: (entry: RecommendedProgramme) => void | Promise<void>
  onRecord?: (entry: RecommendedProgramme) => void | Promise<void>
}

function ToolCard({ tool, onPlan, onRecord }: ToolCardProps) {
  const { t, lang } = useI18n()
  if (tool.name === 'recommend_programme') {
    return (
      <RecommendCard tool={tool} lang={lang} onPlan={onPlan} onRecord={onRecord} />
    )
  }
  if (tool.name === 'record_programme') {
    const title = String(tool.args.title ?? '')
    const channel =
      (tool.result?.recording as { channel_name?: string })?.channel_name ??
      String(tool.args.channel_id ?? '')
    const ok = tool.result?.ok !== false
    return (
      <div
        className={cn(
          'flex items-start gap-3 rounded-2xl border px-3 py-2.5 text-[12px]',
          ok
            ? 'border-[var(--color-cyan-primary)]/30 bg-[var(--color-cyan-primary)]/[0.07]'
            : 'border-[var(--color-rose-primary)]/40 bg-[var(--color-rose-primary)]/[0.08] text-[var(--color-rose-primary)]',
        )}
      >
        <div className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-current" />
        <div className="min-w-0 flex-1 leading-snug">
          <div className="text-[11px] uppercase tracking-[0.18em] opacity-70">
            {ok ? t('ai_recording_started') : t('error')}
          </div>
          <div className="mt-0.5 font-medium text-white">{title}</div>
          <div className="text-[11px] text-fog-200/60">{channel}</div>
          {!ok && tool.result?.error !== undefined && (
            <div className="mt-1">{String(tool.result.error)}</div>
          )}
        </div>
      </div>
    )
  }
  if (tool.name === 'list_recordings') {
    const count = Number((tool.result as { count?: number } | undefined)?.count ?? 0)
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[12px] text-fog-200/70">
        📼 {count} {t('archive_title').toLowerCase()}
      </div>
    )
  }
  return null
}

interface RecommendCardProps {
  tool: ToolEvent
  lang: string
  onPlan?: (entry: RecommendedProgramme) => void | Promise<void>
  onRecord?: (entry: RecommendedProgramme) => void | Promise<void>
}

function RecommendCard({ tool, lang, onPlan, onRecord }: RecommendCardProps) {
  const res = (tool.result ?? {}) as {
    ok?: boolean
    channel_id?: string
    channel_name?: string
    title?: string
    start?: string
    stop?: string
    blurb?: string
    poster_url?: string | null
  }
  const channelId = res.channel_id ?? String(tool.args.channel_id ?? '')
  const title = res.title ?? String(tool.args.title ?? '')
  const channelName = res.channel_name ?? channelId
  const start = res.start ?? String(tool.args.start ?? '')
  const stop = res.stop ?? String(tool.args.stop ?? '')
  const blurb = res.blurb ?? String(tool.args.blurb ?? '')
  const posterUrl = res.poster_url ?? null

  // Skeleton while the tool is still resolving (no result yet).
  if (!res.title && !tool.result) {
    return (
      <div className="h-24 w-full animate-pulse rounded-2xl border border-white/10 bg-white/[0.03]" />
    )
  }
  // Backend rejected the tool call (e.g. hallucinated channel_id) — render
  // nothing rather than a half-broken card. The model usually retries with a
  // correct id in the next round.
  if (tool.result && res.ok === false) {
    return null
  }

  const when = start
    ? new Date(start).toLocaleTimeString(lang === 'ru' ? 'ru-RU' : 'en-GB', {
        hour: '2-digit',
        minute: '2-digit',
      })
    : ''
  const durMin =
    start && stop
      ? Math.max(
          1,
          Math.round(
            (new Date(stop).getTime() - new Date(start).getTime()) / 60000,
          ),
        )
      : 0
  const now = useNow(30_000)
  const countdown = start ? formatCountdown(start, now, lang) : ''

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="group relative flex gap-3 overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03] p-2.5 transition hover:border-white/20"
    >
      {/* Poster */}
      <div className="relative h-24 w-16 shrink-0 overflow-hidden rounded-xl bg-white/[0.04]">
        {posterUrl ? (
          <img
            src={posterUrl}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        ) : channelId ? (
          <img
            src={api.logoUrl(channelId)}
            alt=""
            loading="lazy"
            className="h-full w-full scale-90 object-contain opacity-60"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        ) : null}
      </div>

      {/* Content */}
      <div className="flex min-w-0 flex-1 flex-col justify-between gap-1.5 py-0.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-fog-200/60">
            {when && (
              <span className="inline-flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {when}
                {durMin > 0 && ` · ${durMin}${lang === 'ru' ? 'м' : 'm'}`}
              </span>
            )}
            {countdown && (
              <span className="rounded-full bg-[var(--color-indigo-primary)]/15 px-1.5 py-0.5 text-[10px] normal-case tracking-normal text-[var(--color-indigo-primary)]">
                {countdown}
              </span>
            )}
            {channelId && (
              <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full bg-white/[0.06] pl-0.5 pr-2 normal-case tracking-normal">
                <img
                  src={api.logoUrl(channelId)}
                  alt=""
                  aria-hidden
                  loading="lazy"
                  className="h-4 w-4 shrink-0 rounded-full bg-black/40 object-contain p-0.5"
                  onError={(e) => {
                    const el = e.currentTarget as HTMLImageElement
                    el.style.visibility = 'hidden'
                  }}
                />
                <span className="truncate text-[11px] text-white/85">
                  {channelName}
                </span>
              </span>
            )}
          </div>
          <div className="mt-1 line-clamp-2 text-[13.5px] font-semibold leading-tight text-white">
            {title}
          </div>
          {blurb && (
            <div className="mt-1 line-clamp-2 text-[12px] leading-snug text-fog-100/80">
              {blurb}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {onPlan && channelId && start && stop && (
            <PlanButton
              onPlan={onPlan}
              entry={{
                channel_id: channelId,
                title,
                start,
                stop,
                poster_keywords: String(tool.args.poster_keywords ?? ''),
                blurb,
              }}
            />
          )}
          {onRecord && channelId && start && stop && (
            <RecordButton
              onRecord={onRecord}
              entry={{ channel_id: channelId, title, start, stop }}
            />
          )}
        </div>
      </div>
    </motion.div>
  )
}

interface PlanButtonProps {
  onPlan: (entry: RecommendedProgramme) => void | Promise<void>
  entry: RecommendedProgramme
}

function PlanButton({ onPlan, entry }: PlanButtonProps) {
  const { t } = useI18n()
  const [state, setState] = useState<'idle' | 'saving' | 'done'>('idle')
  const click = async () => {
    if (state !== 'idle') return
    setState('saving')
    try {
      await onPlan(entry)
      setState('done')
    } catch {
      setState('idle')
    }
  }
  return (
    <button
      type="button"
      onClick={click}
      disabled={state !== 'idle'}
      aria-live="polite"
      className={cn(
        'flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition',
        state === 'done'
          ? 'border border-[var(--color-cyan-primary)]/50 bg-[var(--color-cyan-primary)]/[0.15] text-[var(--color-cyan-primary)]'
          : state === 'saving'
            ? 'bg-white/60 text-black/70'
            : 'bg-white/90 text-black hover:bg-white',
      )}
    >
      {state === 'saving' ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : state === 'done' ? (
        <Check className="h-3 w-3" strokeWidth={3} />
      ) : (
        <BookmarkCheck className="h-3 w-3" />
      )}
      {state === 'done' ? t('plans_status_scheduled') : t('digest_watch')}
    </button>
  )
}

interface RecordButtonProps {
  onRecord: (entry: RecommendedProgramme) => void | Promise<void>
  entry: RecommendedProgramme
}

function RecordButton({ onRecord, entry }: RecordButtonProps) {
  const { t } = useI18n()
  const [state, setState] = useState<'idle' | 'saving' | 'done'>('idle')
  const click = async () => {
    if (state !== 'idle') return
    setState('saving')
    try {
      await onRecord(entry)
      setState('done')
    } catch {
      setState('idle')
    }
  }
  return (
    <button
      type="button"
      onClick={click}
      disabled={state !== 'idle'}
      aria-live="polite"
      className={cn(
        'flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] transition',
        state === 'done'
          ? 'border border-[var(--color-cyan-primary)]/50 bg-[var(--color-cyan-primary)]/[0.15] text-[var(--color-cyan-primary)]'
          : state === 'saving'
            ? 'border border-white/15 bg-white/[0.04] text-white/60'
            : 'border border-white/15 bg-white/[0.04] text-white/85 hover:border-white/30 hover:text-white',
      )}
    >
      {state === 'saving' ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : state === 'done' ? (
        <Check className="h-3 w-3" strokeWidth={3} />
      ) : (
        <Video className="h-3 w-3" />
      )}
      {state === 'done' ? t('archive_queued') : t('digest_record')}
    </button>
  )
}
