import type { Comic } from './types'
import type { LibraryEntry } from './library-groups'
import {
  ageRatings, collectionMode, collectionModes, colors, comicGenres, customTags, displayTitle,
  publicationFormats, releasePlatforms, releaseTypes, statusLabels, textVersions,
} from './types'

type Option = { label: string; value: string }
type FieldKind = 'text' | 'choice' | 'number'

function options(values: readonly string[]): Option[] {
  return values.map(value => ({ label: value, value }))
}

export const libraryFilterFields = [
  { key: 'title', label: '作品名称', kind: 'text' },
  { key: 'authors', label: '作者', kind: 'text' },
  { key: 'series', label: '系列名称', kind: 'text' },
  { key: 'work_group', label: '作品组', kind: 'text' },
  { key: 'number', label: '册号', kind: 'text' },
  { key: 'tags', label: '标签', kind: 'text' },
  { key: 'publisher', label: '出版方 / 发行社团', kind: 'text' },
  { key: 'translator', label: '译者 / 汉化组', kind: 'text' },
  { key: 'original_works', label: '原作', kind: 'text' },
  { key: 'characters', label: '角色', kind: 'text' },
  { key: 'language', label: '语言代码', kind: 'text' },
  { key: 'genre', label: '内容类型', kind: 'choice', choices: options(comicGenres) },
  { key: 'collection_mode', label: '作品类型', kind: 'choice', choices: collectionModes },
  { key: 'release_type', label: '发行性质', kind: 'choice', choices: options(releaseTypes) },
  { key: 'publication_format', label: '出版形态', kind: 'choice', choices: options(publicationFormats) },
  { key: 'release_platforms', label: '发布平台', kind: 'choice', choices: options(releasePlatforms) },
  { key: 'text_version', label: '文字版本', kind: 'choice', choices: options(textVersions) },
  { key: 'age_rating', label: '年龄分级', kind: 'choice', choices: ageRatings },
  { key: 'color', label: '色彩', kind: 'choice', choices: options(colors) },
  { key: 'status', label: '审核状态', kind: 'choice', choices: Object.entries(statusLabels).map(([value, label]) => ({ label, value })) },
  { key: 'source', label: '来源类型', kind: 'choice', choices: [
    { value: 'folder', label: '本地图片目录' }, { value: 'cbz', label: '本地 CBZ 文件' }, { value: 'komga', label: 'Komga' },
  ] },
  { key: 'pages', label: '页数', kind: 'number' },
  { key: 'year', label: '发行年份', kind: 'number' },
] as const satisfies readonly { key: string; label: string; kind: FieldKind; choices?: Option[] }[]

export type LibraryFilterKey = typeof libraryFilterFields[number]['key']
export type LibraryFilterField = { key: LibraryFilterKey; label: string; kind: FieldKind; choices?: Option[] }
export type LibraryOperator = 'contains' | 'eq' | 'neq' | 'missing' | 'present' | 'gte' | 'lte' | 'between'
export interface LibraryCondition { field: LibraryFilterKey; operator: LibraryOperator; value: string; upper: string }

const fieldDefinitions = new Map<LibraryFilterKey, LibraryFilterField>(libraryFilterFields.map(field => [field.key, field]))

export function libraryFilterField(key: LibraryFilterKey) { return fieldDefinitions.get(key)! }

const operatorLabels: Readonly<Record<LibraryOperator, string>> = {
  contains: '包含', eq: '等于', neq: '不等于', missing: '未填写 / 未确认', present: '已填写 / 已确认',
  gte: '大于或等于', lte: '小于或等于', between: '在范围内',
}
const operators: Readonly<Record<FieldKind, readonly LibraryOperator[]>> = {
  text: ['contains', 'eq', 'neq', 'missing', 'present'],
  choice: ['eq', 'neq', 'missing', 'present'],
  number: ['eq', 'gte', 'lte', 'between', 'missing', 'present'],
}
export function libraryOperators(field: LibraryFilterKey) {
  const allowed = operators[libraryFilterField(field).kind]
  return allowed.filter(operator => !['source', 'status', 'collection_mode'].includes(field) || !isPresenceOperator(operator))
    .map(value => ({ value, label: operatorLabels[value] }))
}
export function isPresenceOperator(operator: LibraryOperator) { return operator === 'missing' || operator === 'present' }

const normalize = (text: string) => text.trim().normalize('NFKC').toLocaleLowerCase()
const flatten = (items: readonly unknown[]): string[] => items.flatMap(item => Array.isArray(item) ? item : [item])
  .filter((item): item is string => typeof item === 'string' && !!item.trim())
const normalizeNumber = (value: string) => /^\d+$/u.test(normalize(value)) ? Number(normalize(value)) : Number.NaN
function asNumber(field: LibraryFilterKey, value: string) {
  const n = normalizeNumber(value)
  return Number.isSafeInteger(n) && n >= (field === 'year' ? 1 : 0) && n <= (field === 'year' ? 9999 : 10000000)
    ? n : null
}

export function validLibraryCondition(condition: LibraryCondition): boolean {
  const field = fieldDefinitions.get(condition.field)
  if (!field || !libraryOperators(field.key).some(option => option.value === condition.operator)) return false
  if (isPresenceOperator(condition.operator)) return true
  if (field.kind === 'choice') return !!field.choices?.some(choice => choice.value === condition.value)
  if (field.kind === 'number') {
    const value = asNumber(field.key, condition.value)
    if (value === null) return false
    const upper = asNumber(field.key, condition.upper)
    return condition.operator !== 'between' || (upper !== null && upper >= value)
  }
  return !!condition.value.trim()
}

export function activeLibraryConditions(conditions: readonly LibraryCondition[]) {
  return conditions.filter(validLibraryCondition)
}

function fieldValues(comic: Comic, field: LibraryFilterKey): string[] {
  const metadata = comic.metadata
  switch (field) {
    case 'title': return flatten([metadata.title, metadata.original_title, metadata.chinese_title])
    case 'series': return flatten([metadata.series, metadata.localized_series])
    case 'tags': return flatten([metadata.tags, customTags(metadata)])
    case 'status': return [comic.status]
    case 'source': return [comic.kind]
    case 'collection_mode': return [collectionMode(metadata)]
    case 'pages': return comic.page_count > 0 ? [String(comic.page_count)] : []
    default: return flatten([metadata[field]])
  }
}

export function matchesLibraryConditions(comic: Comic, conditions: readonly LibraryCondition[]): boolean {
  return conditions.every(({ field, operator, value, upper }) => {
    const values = fieldValues(comic, field).map(normalize)
    if (isPresenceOperator(operator)) {
      const present = values.some(item => field === 'year' ? asNumber(field, item) !== null
        : !(field === 'age_rating' && item === 'unknown'))
      return operator === 'present' ? present : !present
    }
    if (!values.length) return false
    if (libraryFilterField(field).kind === 'number') {
      const current = asNumber(field, values[0])
      const target = asNumber(field, value)
      if (current === null || target === null) return false
      if (operator === 'gte') return current >= target
      if (operator === 'lte') return current <= target
      if (operator === 'between') {
        const last = asNumber(field, upper)
        return last !== null && current >= target && current <= last
      }
      return current === target
    }
    const target = normalize(value)
    if (operator === 'contains') return values.some(item => item.includes(target))
    const exact = values.some(item => item === target)
    return operator === 'neq' ? !exact : exact
  })
}

export const librarySortFields = [
  { key: 'title', label: '作品名称' }, { key: 'authors', label: '作者' }, { key: 'pages', label: '页数' },
  { key: 'status', label: '审核状态' }, { key: 'series', label: '系列名称' }, { key: 'genre', label: '内容类型' },
  { key: 'releaseDate', label: '发行日期' }, { key: 'updatedAt', label: '最近更新时间' },
] as const
export type LibrarySortKey = typeof librarySortFields[number]['key']
export type LibrarySort = { key: LibrarySortKey; direction: 'asc' | 'desc' } | null

export function nextLibrarySort(sort: LibrarySort, key: LibrarySortKey): LibrarySort {
  if (sort?.key !== key) return { key, direction: 'asc' }
  return sort.direction === 'asc' ? { key, direction: 'desc' } : null
}

const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const statusOrder: Record<Comic['status'], number> = { draft: 1, approved: 2, packaged: 3 }
type SortValue = string | number | null
function releaseDate(comic: Comic): number | null {
  const { year, month, day } = comic.metadata
  const y = asNumber('year', year)
  if (!y) return null
  const m = normalizeNumber(month)
  const d = normalizeNumber(day)
  return y * 10000 + (m >= 1 && m <= 12 ? m * 100 : 0) + (d >= 1 && d <= 31 ? d : 0)
}

function sortValue(comic: Comic, field: LibrarySortKey): SortValue {
  switch (field) {
    case 'title': return fieldValues(comic, field).length ? displayTitle(comic.metadata) : null
    case 'authors': return fieldValues(comic, field).join('、') || null
    case 'pages': return comic.page_count > 0 ? comic.page_count : null
    case 'status': return statusOrder[comic.status] ?? null
    case 'series': return comic.metadata.series.trim() || comic.metadata.localized_series.trim() || null
    case 'genre': return comic.metadata.genre.trim() || null
    case 'releaseDate': return releaseDate(comic)
    case 'updatedAt': {
      const date = Date.parse(comic.updated_at)
      return Number.isNaN(date) ? null : date
    }
  }
}

function compare(a: SortValue, b: SortValue, direction: NonNullable<LibrarySort>['direction']) {
  if (a === null) return b === null ? 0 : 1
  if (b === null) return -1
  const result = typeof a === 'number' && typeof b === 'number' ? a - b : collator.compare(String(a), String(b))
  return direction === 'asc' ? result : -result
}

export function sortLibraryEntries(entries: LibraryEntry[], sort: LibrarySort): LibraryEntry[] {
  if (!sort) return entries
  const values = new Map<string, SortValue>()
  const bookValue = (comic: Comic) => {
    if (!values.has(comic.id)) values.set(comic.id, sortValue(comic, sort.key))
    return values.get(comic.id)!
  }
  return entries.map(entry => {
    if (entry.kind === 'comic') return { entry, value: bookValue(entry.comic) }
    const visibleMembers = [...entry.visibleMembers].sort((a, b) => compare(bookValue(a), bookValue(b), sort.direction))
    const value = sort.key === 'title' ? entry.title : visibleMembers.length ? bookValue(visibleMembers[0]) : null
    return { entry: { ...entry, visibleMembers }, value }
  }).sort((a, b) => compare(a.value, b.value, sort.direction)).map(({ entry }) => entry)
}
