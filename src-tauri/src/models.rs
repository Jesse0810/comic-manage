use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Comic {
    pub id: i64,
    pub local_path: String,
    pub title: String,
    pub status: String,
    pub source_id: Option<String>,
    pub cover_url: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ScrapeCandidate {
    pub source_id: String,
    pub source_name: String,
    pub title: String,
    pub cover_url: Option<String>,
    pub score: f32,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AppSettings {
    pub default_library_dir: String,
    pub request_interval_ms: i64,
    pub user_agent: String,
    pub save_cover: bool,
    pub db_path: String,
}
