"""Job scanner package — portal scrapers with stealth + human-like pacing."""

from job_scanner.base import BaseScraper
from job_scanner.models import ScrapedJob
from job_scanner.portals.foundit import FounditScraper
from job_scanner.portals.internshala import InternshalaScraper
from job_scanner.portals.linkedin import LinkedInScraper
from job_scanner.portals.naukri import NaukriScraper
from job_scanner.portals.unstop import UnstopScraper

__all__ = [
    "BaseScraper",
    "ScrapedJob",
    "FounditScraper",
    "InternshalaScraper",
    "LinkedInScraper",
    "NaukriScraper",
    "UnstopScraper",
]
