import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, FileText, Globe, Image, List, ListOrdered, Moon, Play, RefreshCw, Settings2, Sun, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../lib/api'
import { useI18n } from '../lib/i18n'
import { LogoManagerDialog } from './LogoManagerDialog'
import { cn } from '../lib/cn'
import { useIsMobile } from '../hooks/useIsMobile'
import type { Theme } from '../hooks/useTheme'

interface HeaderProps {
  duplicatesCount: number
  theme: Theme
  onToggleTheme: () => void
  onReload: () => void
  onShowDuplicates: () => void
  /** Refetch UI data only, without asking the backend to reload the file. */
  onRefetchData: () => Promise<void>
}

export function Header({ duplicatesCount, theme, onToggleTheme, onReload, onShowDuplicates, onRefetchData }: HeaderProps) {
  const isMobile = useIsMobile()
  const { t } = useI18n()
  const [clearing, setClearing] = useState(false)
  const [showImportDialog, setShowImportDialog] = useState(false)
  const [showDefaultOrderDialog, setShowDefaultOrderDialog] = useState(false)
  const [showLogoManager, setShowLogoManager] = useState(false)
  const [showSettingsMenu, setShowSettingsMenu] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)

  const settingsBtnRef = useRef<HTMLButtonElement>(null)
  const settingsMenuRef = useRef<HTMLDivElement>(null)
  const [settingsMenuPos, setSettingsMenuPos] = useState({ top: 0, right: 0 })

  useLayoutEffect(() => {
    if (showSettingsMenu && settingsBtnRef.current) {
      const r = settingsBtnRef.current.getBoundingClientRect()
      setSettingsMenuPos({ top: r.bottom + 6, right: window.innerWidth - r.right })
    }
  }, [showSettingsMenu])

  useEffect(() => {
    if (!showSettingsMenu) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (settingsMenuRef.current?.contains(target) || settingsBtnRef.current?.contains(target)) return
      setShowSettingsMenu(false)
      setConfirmClear(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showSettingsMenu])

  const handleClearConfirm = async () => {
    setConfirmClear(false)
    setShowSettingsMenu(false)
    setClearing(true)
    try {
      await api.clearState()
      await onReload()
    } finally {
      setClearing(false)
    }
  }

  return (
    <>
      <header className="glass-strong flex items-center justify-between gap-3 border-b border-white/5 px-3 py-2 sm:px-5 sm:py-3">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <motion.div
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-[var(--color-indigo-primary)] to-[var(--color-cyan-primary)] shadow-[0_6px_18px_-6px_rgba(212,165,86,0.5)]"
          >
            <Play className="h-3.5 w-3.5 fill-white text-white" strokeWidth={0} />
          </motion.div>
          <h1 className="text-[14px] font-semibold leading-none text-white">m3u Studio</h1>
        </div>

        {/* Right controls */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onToggleTheme}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-fog-200 transition hover:border-white/20 hover:bg-white/10 hover:text-white"
            title={theme === 'dark' ? t('switch_to_light') : t('switch_to_dark')}
            aria-label="Toggle theme"
          >
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          {!isMobile && (
            <button
              ref={settingsBtnRef}
              type="button"
              onClick={() => { setShowSettingsMenu((v) => !v); setConfirmClear(false) }}
              className={cn(
                'relative flex h-9 w-9 items-center justify-center rounded-lg border transition',
                showSettingsMenu
                  ? 'border-white/20 bg-white/10 text-white'
                  : 'border-white/10 bg-white/5 text-fog-200 hover:border-white/20 hover:bg-white/10 hover:text-white',
              )}
              title={t('settings')}
            >
              <Settings2 className="h-4 w-4" />
              {duplicatesCount > 0 && (
                <span className="absolute -right-1 -top-1 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-amber-500 px-0.5 text-[8px] font-bold text-black">
                  {duplicatesCount}
                </span>
              )}
            </button>
          )}
        </div>
      </header>

      {/* Settings menu portal */}
      {createPortal(
        <AnimatePresence>
          {showSettingsMenu && (
            <motion.div
              ref={settingsMenuRef}
              initial={{ opacity: 0, y: -6, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6, scale: 0.97 }}
              transition={{ duration: 0.12 }}
              style={{ top: settingsMenuPos.top, right: settingsMenuPos.right }}
              className="fixed z-[9999] w-56 overflow-hidden rounded-xl border border-white/10 bg-[#181620] shadow-2xl"
            >
              {/* Import */}
              <button
                type="button"
                onClick={() => { setShowImportDialog(true); setShowSettingsMenu(false) }}
                className="flex w-full items-center gap-3 px-4 py-3 text-[13px] text-fog-200 transition hover:bg-white/5 hover:text-white"
              >
                <Upload className="h-4 w-4 shrink-0 text-fog-100/50" />
                <span className="font-medium">{t('import_playlist')}</span>
              </button>

              <div className="mx-4 border-t border-white/5" />

              {/* Export */}
              <a
                href={api.exportUrl()}
                download="playlist_main.m3u8"
                onClick={() => setShowSettingsMenu(false)}
                className="flex items-center gap-3 px-4 py-3 text-[13px] text-fog-200 transition hover:bg-white/5 hover:text-white"
              >
                <List className="h-4 w-4 shrink-0 text-[var(--color-indigo-primary)]" />
                <div>
                  <div className="font-medium">{t('export_playlist')}</div>
                  <div className="text-[11px] text-fog-100/40">playlist_main.m3u8</div>
                </div>
              </a>
              <a
                href={api.exportNamesUrl()}
                download="main_channels.txt"
                onClick={() => setShowSettingsMenu(false)}
                className="flex items-center gap-3 px-4 py-3 text-[13px] text-fog-200 transition hover:bg-white/5 hover:text-white"
              >
                <FileText className="h-4 w-4 shrink-0 text-[var(--color-cyan-primary)]" />
                <div>
                  <div className="font-medium">{t('export_channel_list')}</div>
                  <div className="text-[11px] text-fog-100/40">main_channels.txt</div>
                </div>
              </a>

              <button
                type="button"
                onClick={() => { setShowDefaultOrderDialog(true); setShowSettingsMenu(false) }}
                className="flex w-full items-center gap-3 px-4 py-3 text-[13px] text-fog-200 transition hover:bg-white/5 hover:text-white"
              >
                <ListOrdered className="h-4 w-4 shrink-0 text-fog-100/50" />
                <div className="min-w-0 flex-1 text-left">
                  <div className="font-medium">{t('default_channel_order')}</div>
                  <div className="text-[11px] text-fog-100/40">{t('used_on_first_import')}</div>
                </div>
              </button>

              <button
                type="button"
                onClick={() => { setShowLogoManager(true); setShowSettingsMenu(false) }}
                className="flex w-full items-center gap-3 px-4 py-3 text-[13px] text-fog-200 transition hover:bg-white/5 hover:text-white"
              >
                <Image className="h-4 w-4 shrink-0 text-fog-100/50" />
                <div className="min-w-0 flex-1 text-left">
                  <div className="font-medium">{t('channel_logos')}</div>
                  <div className="text-[11px] text-fog-100/40">{t('manage_logos')}</div>
                </div>
              </button>

              <div className="mx-4 border-t border-white/5" />

              {/* Duplicates */}
              <button
                type="button"
                onClick={() => { onShowDuplicates(); setShowSettingsMenu(false) }}
                className={cn(
                  'flex w-full items-center gap-3 px-4 py-3 text-[13px] transition',
                  duplicatesCount > 0
                    ? 'text-amber-400 hover:bg-amber-500/10'
                    : 'text-fog-200/50 hover:bg-white/5 hover:text-fog-200',
                )}
              >
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <div className="flex-1 text-left">
                  <div className="font-medium">{t('duplicates')}</div>
                  <div className={cn('text-[11px]', duplicatesCount > 0 ? 'text-amber-400/50' : 'text-fog-100/40')}>{t('find_similar')}</div>
                </div>
                {duplicatesCount > 0 && (
                  <span className="rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-400">
                    {duplicatesCount}
                  </span>
                )}
              </button>

              {/* Language */}
              <LanguageSelector />

              <div className="mx-4 border-t border-white/5" />

              {/* Clear state */}
              <AnimatePresence mode="wait" initial={false}>
                {confirmClear ? (
                  <motion.div
                    key="confirm"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.1 }}
                    className="flex items-center gap-2 px-4 py-3"
                  >
                    <span className="flex-1 text-[12px] text-[var(--color-rose-primary)]">{t('clear_confirm')}</span>
                    <button
                      type="button"
                      onClick={handleClearConfirm}
                      className="rounded px-2.5 py-1 text-[11px] font-semibold text-[var(--color-rose-primary)] hover:bg-[var(--color-rose-primary)]/20"
                    >
                      {t('yes')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmClear(false)}
                      className="rounded px-2 py-1 text-[11px] font-semibold text-fog-200 hover:bg-white/10"
                    >
                      {t('no')}
                    </button>
                  </motion.div>
                ) : (
                  <motion.button
                    key="clear"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.1 }}
                    type="button"
                    onClick={() => setConfirmClear(true)}
                    disabled={clearing}
                    className={cn(
                      'flex w-full items-center gap-3 px-4 py-3 text-[13px] text-fog-200/50 transition',
                      'hover:bg-[var(--color-rose-primary)]/8 hover:text-[var(--color-rose-primary)]',
                      clearing && 'cursor-not-allowed opacity-40',
                    )}
                  >
                    <Trash2 className="h-4 w-4 shrink-0" />
                    <span className="font-medium">{t('clear_list')}</span>
                  </motion.button>
                )}
              </AnimatePresence>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}

      <AnimatePresence>
        {showImportDialog && (
          <ImportDialog
            onClose={() => setShowImportDialog(false)}
            onImported={onRefetchData}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showDefaultOrderDialog && (
          <DefaultOrderDialog onClose={() => setShowDefaultOrderDialog(false)} />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showLogoManager && (
          <LogoManagerDialog onClose={() => setShowLogoManager(false)} />
        )}
      </AnimatePresence>
    </>
  )
}

interface DefaultOrderDialogProps {
  onClose: () => void
}

function DefaultOrderDialog({ onClose }: DefaultOrderDialogProps) {
  const { t } = useI18n()
  const [names, setNames] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.getDefaultNames()
      .then((res) => setNames(res.names.join('\n')))
      .catch(() => setNames(''))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      const nameList = names.split('\n').map((n) => n.trim()).filter(Boolean)
      await api.setDefaultNames(nameList)
      onClose()
    } catch (err) {
      alert(`Save error: ${err}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 12 }}
        transition={{ duration: 0.18 }}
        className="glass w-full max-w-md rounded-2xl p-6 shadow-2xl"
      >
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-semibold text-white">{t('default_channel_order')}</h2>
            <p className="mt-0.5 text-[12px] text-fog-100/50">{t('used_on_first_import')}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-fog-100/60 transition hover:bg-white/10 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-6">
          <label className="mb-2 block text-[11px] font-semibold uppercase tracking-wider text-fog-100/60">
            {t('channel_list')}
          </label>
          {loading ? (
            <div className="flex h-[200px] items-center justify-center text-[13px] text-fog-100/40">
              {t('connecting')}
            </div>
          ) : (
            <textarea
              value={names}
              onChange={(e) => setNames(e.target.value)}
              placeholder={"Channel 1\nChannel 2\nChannel 3\n…"}
              rows={10}
              className={cn(
                'w-full resize-none rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 font-mono text-[13px] text-white',
                'placeholder:font-sans placeholder:text-fog-100/30',
                'focus:border-[var(--color-indigo-primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--color-indigo-primary)]/20',
              )}
            />
          )}
          <p className="mt-1.5 text-[11px] text-fog-100/40">
            {t('channel_list_hint')}
          </p>
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-[13px] font-medium text-fog-200 transition hover:bg-white/10 hover:text-white"
          >
            {t('cancel')}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={loading || saving}
            className={cn(
              'flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-medium text-white transition',
              'bg-gradient-to-r from-[var(--color-indigo-primary)] to-[var(--color-indigo-soft)]',
              'shadow-[0_8px_20px_-8px_rgba(212,165,86,0.6)]',
              'hover:brightness-110',
              (loading || saving) && 'cursor-not-allowed opacity-50',
            )}
          >
            {saving ? (
              <>
                <RefreshCw className="h-4 w-4 animate-spin" />
                Saving…
              </>
            ) : (
              <>
                <ListOrdered className="h-4 w-4" />
                {t('save')}
              </>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

interface ImportDialogProps {
  onClose: () => void
  onImported: () => Promise<void>
}

function ImportDialog({ onClose, onImported }: ImportDialogProps) {
  const { t } = useI18n()
  const [file, setFile] = useState<File | null>(null)
  const [namesFile, setNamesFile] = useState<File | null>(null)
  const [names, setNames] = useState('')
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const namesFileRef = useRef<HTMLInputElement>(null)

  const handleNamesFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null
    e.target.value = ''
    if (!f) return
    setNamesFile(f)
    const text = await f.text()
    setNames(text)
  }

  const handleSubmit = async () => {
    if (!file) return
    setImporting(true)
    try {
      await api.importPlaylist(file, names.trim() || undefined)
      await onImported()
      onClose()
    } catch (err) {
      alert(`Import error: ${err}`)
    } finally {
      setImporting(false)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 12 }}
        transition={{ duration: 0.18 }}
        className="glass w-full max-w-md rounded-2xl p-6 shadow-2xl"
      >
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-semibold text-white">{t('import_playlist')}</h2>
            <p className="mt-0.5 text-[12px] text-fog-100/50">{t('replaces_current')}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-fog-100/60 transition hover:bg-white/10 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4">
          <label className="mb-2 block text-[11px] font-semibold uppercase tracking-wider text-fog-100/60">
            {t('playlist_file')} <span className="text-[var(--color-rose-primary)]">*</span>
          </label>
          <input
            ref={fileRef}
            type="file"
            accept=".m3u,.m3u8,application/vnd.apple.mpegurl,audio/x-mpegurl"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className={cn(
              'flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-[13px] transition',
              file
                ? 'border-[var(--color-indigo-primary)]/50 bg-[var(--color-indigo-primary)]/10 text-white'
                : 'border-white/10 bg-white/[0.03] text-fog-100/50 hover:border-white/20 hover:bg-white/5 hover:text-fog-200',
            )}
          >
            <Upload className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {file ? file.name : t('select_file')}
            </span>
          </button>
        </div>

        <div className="mb-6">
          <div className="mb-2 flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-fog-100/60">
              {t('channel_list')}
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] normal-case text-fog-100/40">
                {t('optional')}
              </span>
            </label>
            <input
              ref={namesFileRef}
              type="file"
              accept=".txt,text/plain"
              className="hidden"
              onChange={handleNamesFileChange}
            />
            <button
              type="button"
              onClick={() => namesFileRef.current?.click()}
              className={cn(
                'flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition',
                namesFile
                  ? 'border-[var(--color-cyan-primary)]/40 bg-[var(--color-cyan-primary)]/10 text-[var(--color-cyan-primary)]'
                  : 'border-white/10 bg-white/5 text-fog-100/50 hover:border-white/20 hover:text-fog-200',
              )}
            >
              <FileText className="h-3 w-3 shrink-0" />
              <span className="max-w-[120px] truncate">
                {namesFile ? namesFile.name : t('upload_file')}
              </span>
            </button>
          </div>
          <textarea
            value={names}
            onChange={(e) => { setNames(e.target.value); if (!e.target.value) setNamesFile(null) }}
            placeholder={"Channel 1\nChannel 2\nChannel 3\n…"}
            rows={6}
            className={cn(
              'w-full resize-none rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 font-mono text-[13px] text-white',
              'placeholder:font-sans placeholder:text-fog-100/30',
              'focus:border-[var(--color-indigo-primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--color-indigo-primary)]/20',
            )}
          />
          <p className="mt-1.5 text-[11px] text-fog-100/40">
            {t('channels_auto_added')}
          </p>
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-[13px] font-medium text-fog-200 transition hover:bg-white/10 hover:text-white"
          >
            {t('cancel')}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!file || importing}
            className={cn(
              'flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-medium text-white transition',
              'bg-gradient-to-r from-[var(--color-indigo-primary)] to-[var(--color-indigo-soft)]',
              'shadow-[0_8px_20px_-8px_rgba(212,165,86,0.6)]',
              'hover:brightness-110',
              (!file || importing) && 'cursor-not-allowed opacity-50',
            )}
          >
            {importing ? (
              <>
                <RefreshCw className="h-4 w-4 animate-spin" />
                {t('importing')}
              </>
            ) : (
              <>
                <Upload className="h-4 w-4" />
                {t('import')}
              </>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

function LanguageSelector() {
  const { lang, setLang, t } = useI18n()
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <Globe className="h-4 w-4 shrink-0 text-fog-100/50" />
      <div className="min-w-0 flex-1 text-left">
        <div className="text-[13px] font-medium text-fog-200">{t('language')}</div>
        <div className="text-[11px] text-fog-100/40">{t('switch_language')}</div>
      </div>
      <div className="flex gap-0.5 rounded-lg border border-white/10 bg-white/[0.03] p-0.5">
        <button
          type="button"
          onClick={() => setLang('en')}
          className={cn(
            'rounded-md px-2 py-0.5 text-[11px] font-medium transition',
            lang === 'en'
              ? 'bg-[var(--color-indigo-primary)]/20 text-[var(--color-indigo-primary)]'
              : 'text-fog-100/50 hover:text-fog-200',
          )}
        >
          EN
        </button>
        <button
          type="button"
          onClick={() => setLang('ru')}
          className={cn(
            'rounded-md px-2 py-0.5 text-[11px] font-medium transition',
            lang === 'ru'
              ? 'bg-[var(--color-indigo-primary)]/20 text-[var(--color-indigo-primary)]'
              : 'text-fog-100/50 hover:text-fog-200',
          )}
        >
          RU
        </button>
      </div>
    </div>
  )
}
