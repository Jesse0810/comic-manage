export interface Tag { id: number; name: string }
export interface Artist { id: number; name: string }
export interface Series { id: number; name: string }

export interface MetadataSource {
  id: string;
  sourceName: string;
  baseUrl: string;
  rateLimitMs: number;
}

export interface Comic {
  id: number;
  localPath: string;
  title: string;
  status: 'pending' | 'matched';
  sourceId?: string;
  coverUrl?: string;
}

export interface ScrapeCandidate {
  sourceId: string;
  sourceName: string;
  title: string;
  coverUrl?: string;
  score: number;
}

export interface ComicMetadata {
  title: string;
  description?: string;
  artists: string[];
  tags: string[];
  series?: string;
  coverUrl?: string;
}

export interface ScrapeTask {
  id: number;
  comicId: number;
  source: string;
  keyword: string;
  status: 'success' | 'failed' | 'running';
  createdAt: string;
}

export interface AppSettings {
  defaultLibraryDir: string;
  requestIntervalMs: number;
  userAgent: string;
  saveCover: boolean;
  dbPath: string;
}
