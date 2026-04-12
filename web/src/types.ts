/**
 * Shared DTOs that mirror server/main.py pydantic models.
 * Keep these in sync with the backend — drift here will crash the UI.
 */

export interface Channel {
  id: string
  name: string
  url: string
  group: string
  tvg_id: string
  has_logo: boolean
  /** Days of archive/timeshift the provider exposes (0 = no archive). */
  catchup_days: number
}

export interface SourceResponse {
  total: number
  groups: Record<string, Channel[]>
}

export interface MainResponse {
  ids: string[]
  channels: Channel[]
}

export type MainOperation =
  | { op: 'reorder'; ids: string[] }
  | { op: 'add'; id: string; position?: number }
  | { op: 'remove'; id: string }
  | { op: 'move'; id: string; to: number }

export type SourceOperation =
  | { op: 'rename_group'; old: string; new: string }
  | { op: 'delete_channel'; id: string }
  | { op: 'move_channel'; id: string; group: string }

export interface Programme {
  title: string
  description: string
  /** ISO-8601 string, timezone-aware */
  start: string
  /** ISO-8601 string, timezone-aware */
  stop: string
}

export interface DuplicateGroup {
  key: string
  reason: 'name' | 'tvg_id'
  channels: Channel[]
}

export interface DuplicatesResponse {
  total: number
  groups: DuplicateGroup[]
}

export interface EpgResponse {
  loaded: boolean
  loading: boolean
  catchup_days: number
  current_index: number | null
  programmes: Programme[]
}
