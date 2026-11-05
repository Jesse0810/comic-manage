export type ComicStatus = 'draft' | 'approved' | 'packaged'
export interface Metadata {
  title: string; series: string; localized_series: string; number: string; count: string; volume: string
  pencillers: string[]; web: string[]; day: string; black_and_white: 'Unknown' | 'Yes' | 'No'
  manga: 'Unknown' | 'Yes' | 'No' | 'YesAndRightToLeft'
  original_works: string[]; characters: string[]; release_type: string; publication_format: string
  original_title: string; chinese_title: string; authors: string[]; publisher: string; translator: string
  genre: string; tags: string[]; translated: boolean; uncensored: boolean; full_color: boolean
  language: string; age_rating: string; summary: string; year: string; month: string; cover_index: number; original_xml: string
}
export interface Comic {
  id: string; source: string; kind: 'folder' | 'cbz'; fingerprint: string; page_count: number
  metadata: Metadata; status: ComicStatus; output: string; warning: string; updated_at: string
}
export interface Bootstrap { comics: Comic[]; roots: string[]; outputRoot: string; dataDir: string }
export interface ImportCandidate {
  id: string; source: string; kind: 'folder' | 'cbz'; page_count: number; metadata: Metadata
  import_status: 'new' | 'existing' | 'removed'; warning: string
}
export interface ImportPreview { token: string; items: ImportCandidate[]; errors: string[]; cancelled?: boolean }
export interface ImportResult { count: number; skipped: number; errors: string[]; cancelled: boolean; ids: string[] }
export const comicGenres = ['同人志', 'CG集', '画集', '设定集', '漫画', '插画集']
export const publicationFormats = ['独立短篇', '单行本', '合集', '总集篇', '再版', '分卷']
export const ageRatings = [{ label: '未确认', value: 'Unknown' }, { label: '全年龄', value: 'Everyone' }, { label: 'R15', value: 'MA15+' }, { label: 'R18', value: 'R18+' }]
export const colorLabels: Record<string, string> = { Unknown: '色彩未确认', Yes: '黑白', No: '彩色 / 非黑白' }
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
  const tags = [...classificationValues(metadata.original_works).map(value => `原作:${value}`), ...classificationValues(metadata.characters).map(value => `角色:${value}`)]
  if (metadata.release_type) tags.push(`发行性质:${metadata.release_type}`)
  if (metadata.publication_format) tags.push(`出版形态:${metadata.publication_format}`)
  if (metadata.age_rating === 'R18+' && metadata.uncensored) tags.push('修正状态:无修正')
  return [...new Set(tags.filter(value => !value.endsWith(':')))]
}
export interface ExportPlan {
  token: string; totalBytes: number
  items: { id: string; title: string; source: string; destination: string; pageCount: number; attachmentCount: number }[]
}
export const statusLabels: Record<ComicStatus, string> = { draft: '待审核', approved: '已审核', packaged: '已生成' }
export function displayTitle(metadata: Metadata): string {
  return (metadata.title ?? (metadata.translated ? metadata.chinese_title : metadata.original_title)).trim() || '未命名作品'
}
export function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) }
export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
