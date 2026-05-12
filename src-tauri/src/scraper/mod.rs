pub mod mock;
use crate::models::ScrapeCandidate;

pub trait ScraperAdapter: Send + Sync {
    fn source_name(&self) -> &str;
    fn base_url(&self) -> &str;
    fn rate_limit_ms(&self) -> u64;
    fn search(&self, keyword: &str) -> Vec<ScrapeCandidate>;
}
