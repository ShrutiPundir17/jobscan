from job_scanner.portals._cli import run_portal_cli
from job_scanner.portals.foundit import FounditScraper

if __name__ == "__main__":
    run_portal_cli(
        scraper_factory=FounditScraper,
        description="Scrape Foundit.in job search results",
        portal_label="Foundit",
    )
