from job_scanner.portals._cli import run_portal_cli
from job_scanner.portals.internshala import InternshalaScraper

if __name__ == "__main__":
    run_portal_cli(
        scraper_factory=InternshalaScraper,
        description="Scrape Internshala jobs/internships",
        portal_label="Internshala",
    )
