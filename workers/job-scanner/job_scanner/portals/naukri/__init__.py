"""Naukri.com portal scraper."""

from job_scanner.portals.naukri.scraper import (
    NaukriScraper,
    build_search_url,
    paginated_url,
    slugify,
)

__all__ = ["NaukriScraper", "build_search_url", "paginated_url", "slugify"]
