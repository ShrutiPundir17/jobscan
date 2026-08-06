from job_scanner.portals._cli import run_portal_cli
from job_scanner.portals.unstop import UnstopScraper

if __name__ == "__main__":
    run_portal_cli(
        scraper_factory=UnstopScraper,
        description="Scrape Unstop jobs/internships",
        portal_label="Unstop",
    )
