use crate::models::ScrapeCandidate;
use super::ScraperAdapter;

pub struct MockScraper;

impl ScraperAdapter for MockScraper {
    fn source_name(&self) -> &str { "MockSource" }
    fn base_url(&self) -> &str { "https://example.com" }
    fn rate_limit_ms(&self) -> u64 { 1200 }
    fn search(&self, keyword: &str) -> Vec<ScrapeCandidate> {
        vec![
            ScrapeCandidate { source_id: "mock-001".into(), source_name: self.source_name().into(), title: format!("{} - 候选A", keyword), cover_url: None, score: 0.93 },
            ScrapeCandidate { source_id: "mock-002".into(), source_name: self.source_name().into(), title: format!("{} - 候选B", keyword), cover_url: None, score: 0.87 }
        ]
    }
}
