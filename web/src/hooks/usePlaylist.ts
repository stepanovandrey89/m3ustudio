/**
 * React Query hooks + optimistic mutation for main playlist edits.
 *
 * The mutation applies optimistic updates to the query cache so drag-and-drop
 * feels instant, then reconciles with the server response.
 */

import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { DuplicatesResponse, MainOperation, MainResponse, SourceOperation, SourceResponse } from '../types'

const KEY_SOURCE = ['source'] as const
const KEY_MAIN = ['main'] as const
const KEY_LOGOS_STATUS = ['logos-status'] as const

export function useSource() {
  return useQuery<SourceResponse>({
    queryKey: KEY_SOURCE,
    queryFn: api.getSource,
  })
}

export function useMain() {
  return useQuery<MainResponse>({
    queryKey: KEY_MAIN,
    queryFn: api.getMain,
  })
}

/**
 * Poll /api/logos/status every 4 s until the warming task is done,
 * then invalidate source + main so has_logo flags refresh automatically.
 */
export function useLogoWarming() {
  const client = useQueryClient()

  const { data } = useQuery<{ warmed: boolean }>({
    queryKey: KEY_LOGOS_STATUS,
    queryFn: () => fetch('/api/logos/status').then((r) => r.json()),
    refetchInterval: (q) => (q.state.data?.warmed ? false : 4_000),
    staleTime: 0,
  })

  useEffect(() => {
    if (data?.warmed) {
      client.refetchQueries({ queryKey: KEY_SOURCE })
      client.refetchQueries({ queryKey: KEY_MAIN })
    }
  }, [data?.warmed, client])
}

export const KEY_DUPLICATES = ['duplicates'] as const

export function useDuplicates() {
  return useQuery<DuplicatesResponse>({
    queryKey: KEY_DUPLICATES,
    queryFn: api.getDuplicates,
    staleTime: 0,
  })
}

export function useSourceMutation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (op: SourceOperation) => api.patchSource(op),
    onSuccess: (data) => {
      client.setQueryData(KEY_SOURCE, data)
    },
  })
}

export function useMainMutation() {
  const client = useQueryClient()

  return useMutation({
    mutationFn: (op: MainOperation) => api.patchMain(op),
    onMutate: async (op) => {
      await client.cancelQueries({ queryKey: KEY_MAIN })
      const previous = client.getQueryData<MainResponse>(KEY_MAIN)
      const source = client.getQueryData<SourceResponse>(KEY_SOURCE)

      if (!previous) return { previous }

      const lookupById = new Map<string, MainResponse['channels'][number]>()
      for (const ch of previous.channels) lookupById.set(ch.id, ch)
      if (source) {
        for (const list of Object.values(source.groups)) {
          for (const ch of list) lookupById.set(ch.id, ch)
        }
      }

      const applyIds = (ids: string[]): MainResponse => {
        const channels = ids
          .map((id) => lookupById.get(id))
          .filter((x): x is MainResponse['channels'][number] => x !== undefined)
        return { ids, channels }
      }

      let nextIds: string[] = previous.ids
      if (op.op === 'reorder') {
        nextIds = op.ids
      } else if (op.op === 'add') {
        const without = previous.ids.filter((id) => id !== op.id)
        const pos = op.position ?? without.length
        nextIds = [...without.slice(0, pos), op.id, ...without.slice(pos)]
      } else if (op.op === 'remove') {
        nextIds = previous.ids.filter((id) => id !== op.id)
      } else if (op.op === 'move') {
        const without = previous.ids.filter((id) => id !== op.id)
        nextIds = [...without.slice(0, op.to), op.id, ...without.slice(op.to)]
      }

      client.setQueryData(KEY_MAIN, applyIds(nextIds))
      return { previous }
    },
    onError: (_err, _op, ctx) => {
      if (ctx?.previous) client.setQueryData(KEY_MAIN, ctx.previous)
    },
    onSettled: (server) => {
      if (server) client.setQueryData(KEY_MAIN, server)
      // Main edits mirror into the source's 'основное' group on the server,
      // so the left panel needs to refetch to reflect the new order.
      client.invalidateQueries({ queryKey: KEY_SOURCE })
    },
  })
}
