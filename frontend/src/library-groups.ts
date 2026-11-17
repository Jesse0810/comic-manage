import type { Comic } from './types'
import { displayTitle } from './types'

export interface LibraryComicRow {
  kind: 'comic'
  key: string
  comic: Comic
  groupKey?: string
}

export interface LibraryGroupRow {
  kind: 'group'
  key: string
  name: string
  title: string
  members: Comic[]
  visibleMembers: Comic[]
}

export type LibraryEntry = LibraryComicRow | LibraryGroupRow
export type LibraryRow = LibraryEntry

/** A work-group name is an exact key within one origin, never a title guess. */
export function libraryGroupKey(comic: Comic): string | null {
  const name = comic.metadata.work_group?.trim()
  if (!name) return null
  if (comic.kind !== 'komga') return JSON.stringify(['local', name])
  const remote = comic.remote
  if (!remote?.serverUrl || !remote.libraryId) return JSON.stringify(['remote-unscoped', comic.id, name])
  return JSON.stringify(['komga', remote.serverUrl, remote.libraryId, name])
}

function comicRow(comic: Comic, groupKey?: string): LibraryComicRow {
  return { kind: 'comic', key: `comic:${comic.id}`, comic, ...(groupKey ? { groupKey } : {}) }
}

/** Keep filtered groups intact using the unfiltered origin's membership. */
export function buildLibraryEntries(comics: Comic[], allComics: Comic[]): LibraryEntry[] {
  const members = new Map<string, Comic[]>()
  for (const comic of allComics) {
    const key = libraryGroupKey(comic)
    if (!key) continue
    const list = members.get(key) || []
    if (!list.some(item => item.id === comic.id)) list.push(comic)
    members.set(key, list)
  }
  const visible = new Map<string, Comic[]>()
  const entries: LibraryEntry[] = []
  for (const comic of comics) {
    const key = libraryGroupKey(comic)
    const full = key ? members.get(key) : undefined
    if (!key || !full || full.length < 2) {
      entries.push(comicRow(comic))
      continue
    }
    const existing = visible.get(key)
    if (existing) {
      if (!existing.some(item => item.id === comic.id)) existing.push(comic)
      continue
    }
    const selected: Comic[] = [comic]
    visible.set(key, selected)
    const commonTitle = displayTitle(full[0].metadata)
    const name = comic.metadata.work_group.trim()
    entries.push({ kind: 'group', key: `group:${key}`, name,
      title: full.every(item => displayTitle(item.metadata) === commonTitle) ? commonTitle : name,
      members: [...full], visibleMembers: selected })
  }
  return entries
}

export const buildLibraryGroups = buildLibraryEntries

export function flattenLibraryRows(entries: LibraryEntry[], expanded: ReadonlySet<string>): LibraryRow[] {
  const rows: LibraryRow[] = []
  for (const entry of entries) {
    rows.push(entry)
    if (entry.kind === 'group' && expanded.has(entry.key)) {
      rows.push(...entry.visibleMembers.map(comic => comicRow(comic, entry.key)))
    }
  }
  return rows
}

export function visibleSelectableIds(rows: LibraryRow[], canSelect: (comic: Comic) => boolean): string[] {
  return [...new Set(rows.filter((row): row is LibraryComicRow => row.kind === 'comic')
    .filter(row => canSelect(row.comic)).map(row => row.comic.id))]
}

export function updateVisibleSelection(selectedIds: string[], visibleIds: string[], checked: boolean): string[] {
  const visible = new Set(visibleIds)
  return checked ? [...new Set([...selectedIds, ...visibleIds])]
    : [...new Set(selectedIds.filter(id => !visible.has(id)))]
}

export function toggleComicSelection(selectedIds: string[], id: string, checked: boolean): string[] {
  return updateVisibleSelection(selectedIds, [id], checked)
}
