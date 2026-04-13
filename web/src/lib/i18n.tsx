import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

export type Lang = 'en' | 'ru'

const STORAGE_KEY = 'm3u_lang_v1'

// --- Group name translation maps ---
// Groups that should NOT be translated (stay as-is in both languages)
const KEEP_AS_IS = new Set(['4K', 'UHD', 'HD', 'FHD', 'SD'])

const GROUP_EN_TO_RU: Record<string, string> = {
  'Main': 'Основное',
  'Movies': 'Кино',
  'Series': 'Сериалы',
  'Sport': 'Спорт',
  'News': 'Новости',
  'Kids': 'Детские',
  'Music': 'Музыка',
  'Entertainment': 'Развлекательные',
  'Educational': 'Познавательные',
  'Adults': 'Взрослые',
  'Regional': 'Региональные',
  'Other': 'Другие',
  'Misc': 'Прочие',
  'Federal': 'Федеральные',
  'Documentary': 'Документальные',
}

const GROUP_RU_TO_EN: Record<string, string> = Object.fromEntries(
  Object.entries(GROUP_EN_TO_RU).map(([en, ru]) => [ru, en])
)
// Aliases — multiple Russian names map to same English
GROUP_RU_TO_EN['Прочие'] = 'Other'

export function translateGroup(name: string, targetLang: Lang): string {
  if (KEEP_AS_IS.has(name.toUpperCase())) return name
  if (targetLang === 'en') return GROUP_RU_TO_EN[name] ?? name
  return GROUP_EN_TO_RU[name] ?? name
}

// --- Translation strings ---
const translations: Record<Lang, Record<string, string>> = {
  en: {
    // Header
    'settings': 'Settings',
    'import_playlist': 'Import playlist',
    'replaces_current': 'Replaces current source',
    'export_playlist': 'Export playlist',
    'export_channel_list': 'Export channel list',
    'default_channel_order': 'Default channel order',
    'used_on_first_import': 'Used on first import',
    'channel_logos': 'Channel logos',
    'manage_logos': 'Manage and resolve logos',
    'duplicates': 'Duplicates',
    'find_similar': 'Find similar channels',
    'clear_list': 'Clear list',
    'clear_confirm': 'Clear list?',
    'yes': 'Yes',
    'no': 'No',
    'language': 'Language',
    'switch_language': 'Interface',
    // Source panel
    'source': 'Source',
    'channels': 'channels',
    'search_channels': 'Search channels…',
    'nothing_found': 'Nothing found',
    'rename_group': 'Rename group',
    'new_group': 'New group',
    'move_up': 'Move up',
    'move_down': 'Move down',
    // Main panel
    'main': 'Main',
    'hold_to_select': 'hold to select · drag to reorder',
    'drop_to_add': 'drop to add',
    'selected': 'selected',
    'channels_in_favorites': 'channels in favorites',
    'remove': 'Remove',
    'done': 'Done',
    'empty_title': 'Empty for now',
    'empty_description': "Pick channels from the source on the left — they'll appear here",
    'drop_here': 'Drop to add',
    // Player
    'archive': 'Archive',
    'live': 'Live',
    'connecting': 'Connecting…',
    'prev_channel': 'Previous channel (←)',
    'next_channel': 'Next channel (→)',
    'pause': 'Pause (Space)',
    'play': 'Play (Space)',
    'add_to_main': 'Add to Main',
    'remove_from_main': 'Remove from Main',
    'delete_channel': 'Delete channel',
    'close': 'Close (Esc)',
    'fullscreen': 'Fullscreen (F)',
    'exit_fullscreen': 'Exit fullscreen (F)',
    'show_guide': 'Show guide (G)',
    'hide_guide': 'Hide guide (G)',
    'show_channels': 'Show channel list',
    'hide_channels': 'Hide channel list',
    'show_now_playing': 'Show now playing',
    'hide_now_playing': 'Hide now playing',
    'fix_audio': 'Fix audio: AC-3 → AAC',
    'disable_transcode': 'Disable transcode',
    'return_to_live': 'Return to live',
    // EPG
    'tv_guide': 'TV Guide',
    'loading_guide': 'Loading guide…',
    'preparing_guide': 'Preparing guide…',
    'guide_unavailable': 'Guide unavailable',
    'no_guide': 'No guide available for this channel',
    'today': 'Today',
    'yesterday': 'Yesterday',
    'tomorrow': 'Tomorrow',
    'offset': 'offset',
    // Logo manager
    'all': 'All',
    'found': 'found',
    'missing': 'missing',
    'pending': 'pending',
    'skipped': 'skipped',
    'retry_failed': 'Retry failed',
    'retry': 'Retry',
    'skip': 'Skip',
    'set_logo_url': 'Set logo URL',
    'tries': 'tries',
    'channel_list': 'Channel list',
    'channel_list_hint': 'Use your preferred sort order, one channel name per line',
    'optional': 'optional',
    'playlist_file': 'Playlist file',
    'select_file': 'Select .m3u8 file…',
    'upload_file': 'Upload file',
    'channels_auto_added': 'Channels from this list will be automatically added to Main in the specified order',
    'possible_duplicates': 'Possible duplicates',
    'no_duplicates': 'No duplicates found',
    'all_unique': 'All channels are unique',
    'analyzing': 'analyzing…',
    'similar_name': 'similar name',
    'dismiss': 'Dismiss',
    'dismiss_title': "Dismiss — don't show again",
    'remove_from_source': 'Remove from source',
    'deletion_warning': 'Deletion removes the channel from the source permanently. Keep the best quality.',
    'save': 'Save',
    'cancel': 'Cancel',
    'import': 'Import',
    'importing': 'Importing…',
    // Theme
    'switch_to_light': 'Switch to light theme',
    'switch_to_dark': 'Switch to dark theme',
    // Misc
    'error': 'Error',
  },
  ru: {
    'settings': 'Настройки',
    'import_playlist': 'Импорт плейлиста',
    'replaces_current': 'Заменяет текущий источник',
    'export_playlist': 'Экспорт плейлиста',
    'export_channel_list': 'Экспорт списка каналов',
    'default_channel_order': 'Порядок каналов по умолчанию',
    'used_on_first_import': 'Используется при первом импорте',
    'channel_logos': 'Логотипы каналов',
    'manage_logos': 'Управление логотипами',
    'duplicates': 'Дубликаты',
    'find_similar': 'Поиск похожих каналов',
    'clear_list': 'Очистить список',
    'clear_confirm': 'Очистить список?',
    'yes': 'Да',
    'no': 'Нет',
    'language': 'Язык',
    'switch_language': 'Интерфейс',
    'source': 'Источник',
    'channels': 'каналов',
    'search_channels': 'Поиск каналов…',
    'nothing_found': 'Ничего не найдено',
    'rename_group': 'Переименовать',
    'new_group': 'Новая группа',
    'move_up': 'Вверх',
    'move_down': 'Вниз',
    'main': 'Основное',
    'hold_to_select': 'удерж. для выбора · перетяните для сортировки',
    'drop_to_add': 'отпустите для добавления',
    'selected': 'выбрано',
    'channels_in_favorites': 'каналов в избранном',
    'remove': 'Удалить',
    'done': 'Готово',
    'empty_title': 'Пока пусто',
    'empty_description': 'Выберите каналы из источника слева — они появятся здесь',
    'drop_here': 'Отпустите для добавления',
    'archive': 'Архив',
    'live': 'Эфир',
    'connecting': 'Подключение…',
    'prev_channel': 'Предыдущий канал (←)',
    'next_channel': 'Следующий канал (→)',
    'pause': 'Пауза (Пробел)',
    'play': 'Воспроизвести (Пробел)',
    'add_to_main': 'Добавить в Основное',
    'remove_from_main': 'Убрать из Основного',
    'delete_channel': 'Удалить канал',
    'close': 'Закрыть (Esc)',
    'fullscreen': 'Полный экран (F)',
    'exit_fullscreen': 'Выйти из полного экрана (F)',
    'show_guide': 'Показать программу (G)',
    'hide_guide': 'Скрыть программу (G)',
    'show_channels': 'Список каналов',
    'hide_channels': 'Скрыть список каналов',
    'show_now_playing': 'Сейчас в эфире',
    'hide_now_playing': 'Скрыть эфир',
    'fix_audio': 'Исправить звук: AC-3 → AAC',
    'disable_transcode': 'Отключить транскодинг',
    'return_to_live': 'Вернуться в эфир',
    'tv_guide': 'ТВ Программа',
    'loading_guide': 'Загрузка программы…',
    'preparing_guide': 'Подготовка программы…',
    'guide_unavailable': 'Программа недоступна',
    'no_guide': 'Для этого канала нет программы',
    'today': 'Сегодня',
    'yesterday': 'Вчера',
    'tomorrow': 'Завтра',
    'offset': 'смещение',
    'all': 'Все',
    'found': 'найдено',
    'missing': 'отсутствует',
    'pending': 'ожидание',
    'skipped': 'пропущено',
    'retry_failed': 'Повторить',
    'retry': 'Повторить',
    'skip': 'Пропустить',
    'set_logo_url': 'Указать URL логотипа',
    'tries': 'попыток',
    'channel_list': 'Список каналов',
    'channel_list_hint': 'Используйте свою любимую сортировку по одному каналу в строке',
    'optional': 'необязательно',
    'playlist_file': 'Файл плейлиста',
    'select_file': 'Выбрать .m3u8 файл…',
    'upload_file': 'Загрузить файл',
    'channels_auto_added': 'Каналы из этого списка будут автоматически добавлены в Основное в указанном порядке',
    'possible_duplicates': 'Возможные дубликаты',
    'no_duplicates': 'Дубликатов не найдено',
    'all_unique': 'Все каналы уникальны',
    'analyzing': 'анализ…',
    'similar_name': 'похожее имя',
    'dismiss': 'Скрыть',
    'dismiss_title': 'Скрыть — больше не показывать',
    'remove_from_source': 'Удалить из источника',
    'deletion_warning': 'Удаление убирает канал из источника навсегда. Оставьте лучшее качество.',
    'save': 'Сохранить',
    'cancel': 'Отмена',
    'import': 'Импорт',
    'importing': 'Импорт…',
    'switch_to_light': 'Светлая тема',
    'switch_to_dark': 'Тёмная тема',
    'error': 'Ошибка',
  },
}

// --- Context ---

interface I18nContextValue {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (key: string) => string
}

const I18nContext = createContext<I18nContextValue>({
  lang: 'en',
  setLang: () => {},
  t: (key) => key,
})

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored === 'ru' || stored === 'en') return stored
    } catch { /* */ }
    return 'en'
  })

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, lang) } catch { /* */ }
  }, [lang])

  const setLang = useCallback((l: Lang) => setLangState(l), [])

  const t = useCallback((key: string): string => {
    return translations[lang][key] ?? translations.en[key] ?? key
  }, [lang])

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  return useContext(I18nContext)
}
