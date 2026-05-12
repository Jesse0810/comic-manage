mod models;
mod scraper;

use std::{path::Path, sync::Mutex};
use rusqlite::{params, Connection};
use tauri::State;
use walkdir::WalkDir;
use models::{AppSettings, Comic, ScrapeCandidate};
use scraper::{mock::MockScraper, ScraperAdapter};

struct AppState {
    db: Mutex<Connection>,
}

fn init_db(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(r#"
CREATE TABLE IF NOT EXISTS comics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  local_path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  source_id TEXT,
  cover_url TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  default_library_dir TEXT NOT NULL DEFAULT '',
  request_interval_ms INTEGER NOT NULL DEFAULT 1200,
  user_agent TEXT NOT NULL DEFAULT 'ComicManage/0.1',
  save_cover INTEGER NOT NULL DEFAULT 1,
  db_path TEXT NOT NULL DEFAULT 'comic-manage.db'
);
INSERT OR IGNORE INTO settings (id) VALUES (1);
"#)
}

#[tauri::command]
fn list_comics(state: State<AppState>) -> Result<Vec<Comic>, String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    let mut stmt = conn.prepare("SELECT id, local_path, title, status, source_id, cover_url FROM comics ORDER BY id DESC").map_err(|e| e.to_string())?;
    stmt.query_map([], |r| Ok(Comic { id: r.get(0)?, local_path: r.get(1)?, title: r.get(2)?, status: r.get(3)?, source_id: r.get(4)?, cover_url: r.get(5)? }))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn scan_library(state: State<AppState>, directory: String) -> Result<(), String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    for entry in WalkDir::new(&directory).min_depth(1).max_depth(2).into_iter().flatten() {
        if entry.file_type().is_dir() {
            let name = entry.file_name().to_string_lossy().to_string();
            let path = entry.path().to_string_lossy().replace('\\', "/");
            conn.execute(
                "INSERT OR IGNORE INTO comics (local_path, title, status) VALUES (?1, ?2, 'pending')",
                params![path, name],
            ).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
fn search_metadata(keyword: String) -> Result<Vec<ScrapeCandidate>, String> {
    let scraper = MockScraper;
    Ok(scraper.search(&keyword))
}

#[tauri::command]
fn get_settings(state: State<AppState>) -> Result<AppSettings, String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    conn.query_row("SELECT default_library_dir, request_interval_ms, user_agent, save_cover, db_path FROM settings WHERE id = 1", [], |r| {
        Ok(AppSettings {
            default_library_dir: r.get(0)?,
            request_interval_ms: r.get(1)?,
            user_agent: r.get(2)?,
            save_cover: r.get::<_, i64>(3)? == 1,
            db_path: r.get(4)?,
        })
    }).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_settings(state: State<AppState>, payload: AppSettings) -> Result<(), String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    conn.execute("UPDATE settings SET default_library_dir=?1, request_interval_ms=?2, user_agent=?3, save_cover=?4, db_path=?5 WHERE id=1",
        params![payload.default_library_dir, payload.request_interval_ms, payload.user_agent, if payload.save_cover {1} else {0}, payload.db_path]).map_err(|e| e.to_string())?;
    Ok(())
}

fn main() {
    let db_path = Path::new("comic-manage.db");
    let conn = Connection::open(db_path).expect("open db failed");
    init_db(&conn).expect("init db failed");

    tauri::Builder::default()
        .manage(AppState { db: Mutex::new(conn) })
        .invoke_handler(tauri::generate_handler![list_comics, scan_library, search_metadata, get_settings, save_settings])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
