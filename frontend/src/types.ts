export type ComicStatus = 'draft' | 'approved' | 'packaged'
export interface Metadata {
  original_title: string; chinese_title: string; authors: string[]; publisher: string; translator: string
  genre: string; tags: string[]; translated: boolean; uncensored: boolean; full_color: boolean
  language: string; age_rating: string; summary: string; year: string; cover_index: number; original_xml: string
}
export interface Comic {
  id: string; source: string; kind: 'folder' | 'cbz'; fingerprint: string; page_count: number
  metadata: Metadata; status: ComicStatus; output: string; warning: string; updated_at: string
}
export interface Bootstrap { comics: Comic[]; roots: string[]; outputRoot: string; dataDir: string }
export interface ExportPlan {
  token: string; totalBytes: number
  items: { id: string; title: string; source: string; destination: string; pageCount: number; attachmentCount: number }[]
}
export const statusLabels: Record<ComicStatus, string> = { draft: '待审核', approved: '已审核', packaged: '已生成' }
export function displayTitle(metadata: Metadata): string {
  return (metadata.translated ? metadata.chinese_title : metadata.original_title).trim() || metadata.original_title || '未命名作品'
}
export function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) }
export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
