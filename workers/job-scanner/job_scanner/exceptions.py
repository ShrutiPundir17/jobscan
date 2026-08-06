class ScraperError(Exception):
    """Base scraper failure."""


class ScraperBlockedError(ScraperError):
    """Portal blocked the session (captcha, ban, hard challenge)."""


class ScraperNavigationError(ScraperError):
    """Failed to open or settle on a page."""
