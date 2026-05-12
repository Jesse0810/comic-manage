import { defineStore } from 'pinia';
import { invoke } from '@tauri-apps/api/core';
import type { AppSettings, Comic, ScrapeCandidate } from '../types/models';

export const useLibraryStore = defineStore('library', {
  state: () => ({
    comics: [] as Comic[],
    candidates: [] as ScrapeCandidate[],
    settings: null as AppSettings | null,
  }),
  getters: {
    matchedCount: (s) => s.comics.filter((c) => c.status === 'matched').length,
    pendingCount: (s) => s.comics.filter((c) => c.status === 'pending').length,
  },
  actions: {
    async loadComics() { this.comics = await invoke<Comic[]>('list_comics'); },
    async scanDirectory(path: string) {
      await invoke('scan_library', { directory: path });
      await this.loadComics();
    },
    async search(keyword: string) {
      this.candidates = await invoke<ScrapeCandidate[]>('search_metadata', { keyword });
    },
    async loadSettings() { this.settings = await invoke<AppSettings>('get_settings'); },
    async saveSettings(payload: AppSettings) {
      await invoke('save_settings', { payload });
      this.settings = payload;
    },
  },
});
