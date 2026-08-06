"""Portal-specific scrapers."""

from job_scanner.portals.foundit import FounditScraper
from job_scanner.portals.internshala import InternshalaScraper
from job_scanner.portals.linkedin import LinkedInScraper
from job_scanner.portals.naukri import NaukriScraper, build_search_url
from job_scanner.portals.unstop import UnstopScraper

__all__ = [
    "FounditScraper",
    "InternshalaScraper",
    "LinkedInScraper",
    "NaukriScraper",
    "UnstopScraper",
    "build_search_url",
]
