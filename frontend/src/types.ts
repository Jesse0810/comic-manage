export type ComicStatus = 'draft' | 'approved' | 'packaged'
export type CollectionMode = 'oneshot' | 'series'
export type Color = '' | '黑白' | '全彩' | '部分彩色'
export type ReadingDirection = '' | 'LEFT_TO_RIGHT' | 'RIGHT_TO_LEFT' | 'VERTICAL' | 'WEBTOON'
export type TextVersion = '' | '有文字' | '无文字'
export interface Metadata {
  title: string; series: string; localized_series: string; localized_series_label: string; collection_mode: CollectionMode
  number: string; count: string; volume: string
  pencillers: string[]; web: string[]; day: string; black_and_white: 'Unknown' | 'Yes' | 'No'
  manga: 'Unknown' | 'Yes' | 'No' | 'YesAndRightToLeft'
  color: Color; release_methods: string[]; release_platforms: string[]; reading_direction: ReadingDirection
  work_group: string; text_version: TextVersion
  original_works: string[]; characters: string[]; release_type: string; publication_format: string
  original_title: string; chinese_title: string; authors: string[]; publisher: string; translator: string
  genre: string; tags: string[]; translated: boolean; uncensored: boolean; full_color: boolean
  language: string; age_rating: string; summary: string; year: string; month: string; cover_index: number; original_xml: string
}
export interface Comic {
  id: string; source: string; kind: 'folder' | 'cbz' | 'komga'; fingerprint: string; page_count: number
  metadata: Metadata; status: ComicStatus; output: string; warning: string; updated_at: string
  remote?: { serverUrl: string; libraryId: string; libraryName: string; bookId: string; seriesId: string; scope: string; hasLocalChanges: boolean; conflict: boolean; syncedAt?: string; unavailable?: boolean; pendingSync?: boolean; dirtyFields?: string[]; sharedMetadata?: { ageRating?: number | null; genres?: string[]; readingDirection?: string; alternateTitles?: { label: string; title: string }[]; [key: string]: unknown } }
}
export interface Bootstrap { comics: Comic[]; roots: string[]; outputRoot: string; dataDir: string; komgaScope?: string; komgaLibraryName?: string }
export interface ImportCandidate {
  id: string; source: string; kind: 'folder' | 'cbz'; page_count: number; metadata: Metadata
  import_status: 'new' | 'existing' | 'removed'; warning: string
}
export interface ImportPreview { token: string; items: ImportCandidate[]; errors: string[]; cancelled?: boolean }
export interface ImportResult { count: number; skipped: number; errors: string[]; cancelled: boolean; ids: string[] }
export interface KomgaConfig { url: string; libraryId: string; libraryName: string; hasApiKey: boolean }
export interface KomgaPullResult { total: number; added: number; updated: number; kept: number; skipped: number; cancelled: boolean; errors: string[]; ids: string[]; scope: string }
export interface KomgaLibrary { id: string; name: string; root?: string }
export interface KomgaConnectionInput { url: string; apiKey?: string; clearKey?: boolean }
export interface KomgaConfigInput extends KomgaConnectionInput { libraryId: string; libraryName: string }
export type KomgaSyncStatus = 'success' | 'skipped' | 'failed'
export interface KomgaSyncItem { id: string; title: string; status: KomgaSyncStatus; message: string; bookId?: string; changed?: boolean; warnings?: string[] }
export interface KomgaSyncResult {
  total: number; updated: number; unchanged?: number; skipped: number; failed: number; seriesUpdated: number; cancelled: boolean
  books: KomgaSyncItem[]; series: KomgaSyncItem[]; error?: string
}
export interface ReviewSyncPreviewItem {
  id: string; title: string; kind: Comic['kind'] | ''; status: 'ready' | 'blocked'; message: string
  authors: string[]; genre: string; ageRating: string; dirtyFields?: string[]
}
export interface ReviewSyncPlan {
  token: string; total: number; ready: number; blocked: number; cancelled: boolean
  items: ReviewSyncPreviewItem[]; warnings: string[]
}
export type ReviewSyncStatus = 'approved' | 'skipped' | 'failed'
export interface ReviewSyncItem { id: string; title: string; status: ReviewSyncStatus; message: string }
export interface ReviewSyncResult {
  total: number; reviewed: number; skipped: number; failed: number; cancelled: boolean
  items: ReviewSyncItem[]; sync: KomgaSyncResult | null; error?: string
}
export const comicGenres = ['漫画', '小说', 'CG集', '画集', '设定集', '插画集']
export const releaseTypes = ['同人发行', '商业出版', '个人独立发布']
export const publicationFormats = ['独立作品', '单行本', '合集', '分卷', '连载']
export const releaseMethods = ['公开发布', '单次购买', '会员订阅', '免费与付费并行']
export const releasePlatforms = ['Fantia', 'pixivFANBOX', 'Ci-en', 'Patreon', 'pixiv', 'BOOTH', 'DLsite', 'FANZA同人', 'DiGiket', 'Gumroad', 'Ko-fi', 'Melonbooks', 'Toranoana']
export const colors = ['黑白', '全彩', '部分彩色']
export const textVersions = ['有文字', '无文字']
export const readingDirections = [{ label: '从左向右', value: 'LEFT_TO_RIGHT' }, { label: '从右向左', value: 'RIGHT_TO_LEFT' }, { label: '纵向', value: 'VERTICAL' }, { label: 'Webtoon', value: 'WEBTOON' }]
export const ageRatings = [{ label: '未确认', value: 'Unknown' }, { label: '全年龄', value: 'Everyone' }, { label: 'MA15+', value: 'MA15+' }, { label: 'R18', value: 'R18+' }]
export const colorLabels: Record<string, string> = { '': '色彩待确认', 黑白: '黑白', 全彩: '全彩', 部分彩色: '部分彩色' }
export const collectionModes = [{ label: '独立作品 · One-Shot', value: 'oneshot' }, { label: '多卷系列 · Series', value: 'series' }]
export const aliasLanguages = [{ label: '简体中文 · zh-Hans', value: 'zh-Hans' }, { label: '繁体中文 · zh-Hant', value: 'zh-Hant' }, { label: '日文 · ja', value: 'ja' }, { label: '英文 · en', value: 'en' }]
export function collectionMode(metadata: Metadata): CollectionMode {
  return metadata.collection_mode ?? (metadata.series.trim() ? 'series' : 'oneshot')
}
export function classificationValues(values: string[]): string[] {
  const seen = new Set<string>()
  return values.flatMap(value => value.split(/[、,，;；\n]/)).map(value => value.trim()).filter(value => {
    const key = value.normalize('NFKC').toLocaleLowerCase()
    if (!value || seen.has(key)) return false
    seen.add(key)
    return true
  })
}
export function customTags(metadata: Metadata): string[] {
  const tags = [...metadata.original_works.map(value => value.trim()).filter(Boolean).map(value => `原作:${value}`), ...classificationValues(metadata.characters).map(value => `角色:${value}`)]
  if (metadata.release_type) tags.push(`发行性质:${metadata.release_type}`)
  if (metadata.publication_format) tags.push(`出版形态:${metadata.publication_format}`)
  for (const method of metadata.release_methods || []) if (method.trim()) tags.push(`发布方式:${method.trim()}`)
  for (const platform of metadata.release_platforms || []) if (platform.trim()) tags.push(`发布平台:${platform.trim()}`)
  if (metadata.color) tags.push(`色彩:${metadata.color}`)
  if (metadata.work_group?.trim()) tags.push(`作品组:${metadata.work_group.trim()}`)
  if (metadata.text_version) tags.push(`文字版本:${metadata.text_version}`)
  if (metadata.age_rating === 'R18+' && metadata.uncensored) tags.push('修正状态:无修正')
  return [...new Set(tags.filter(value => !value.endsWith(':')))]
}
export function versionLabels(metadata: Metadata): string[] {
  return [metadata.work_group?.trim() ? `作品组：${metadata.work_group}` : '', metadata.text_version || ''].filter(Boolean)
}
export interface ExportPlan {
  token: string; totalBytes: number
  items: { id: string; title: string; source: string; destination: string; pageCount: number; attachmentCount: number; warnings: string[] }[]
}
export const statusLabels: Record<ComicStatus, string> = { draft: '待审核', approved: '已审核', packaged: '已生成' }
export function displayTitle(metadata: Metadata): string {
  return (metadata.title ?? (metadata.translated ? metadata.chinese_title : metadata.original_title)).trim() || '未命名作品'
}
export function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) }
export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
