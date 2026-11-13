/// <reference types="vite/client" />
interface ComicDesktop {
  invoke(method: string, params?: Record<string, unknown>): Promise<any>
  onEvent(callback: (event: string, data: any) => void): () => void
  chooseDirectory(): Promise<string | null>
  chooseDirectories(): Promise<string[]>
  chooseCbz(): Promise<string[]>
  chooseBackup(): Promise<string | null>
  chooseKomgaManifest(): Promise<string | null>
  komgaConfig(): Promise<import('./types').KomgaConfig>
  saveKomgaConfig(config: import('./types').KomgaConfigInput): Promise<import('./types').KomgaConfig>
  testKomgaConfig(config: import('./types').KomgaConnectionInput): Promise<{ libraries: import('./types').KomgaLibrary[] }>
  openLocation(kind: 'source' | 'output', id: string): Promise<void>
  openKomgaBook(id: string): Promise<void>
  setWindowState(state: { dirty: boolean; busy: boolean }): void
  closeWindow(): Promise<void>
  getVersion(): Promise<string>
}
interface Window { comic: ComicDesktop }
