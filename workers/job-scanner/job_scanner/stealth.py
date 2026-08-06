from __future__ import annotations

import random
from typing import Any

# Realistic desktop Chrome UA strings (rotate to reduce fingerprint sameness)
USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
]

VIEWPORTS: list[dict[str, int]] = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]

LOCALES = ["en-US", "en-IN", "en-GB"]
TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "Europe/London",
    "Asia/Kolkata",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict[str, int]:
    return dict(random.choice(VIEWPORTS))


def browser_launch_args(
    *,
    headless: bool = True,
    channel: str | None = None,
) -> dict[str, Any]:
    """Playwright chromium.launch kwargs with common anti-automation flags."""
    opts: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-infobars",
            "--window-position=0,0",
        ],
    }
    if channel:
        opts["channel"] = channel
    return opts


def browser_context_options() -> dict[str, Any]:
    """Playwright new_context kwargs for a less bot-like session."""
    viewport = random_viewport()
    return {
        "user_agent": random_user_agent(),
        "viewport": viewport,
        "locale": random.choice(LOCALES),
        "timezone_id": random.choice(TIMEZONES),
        "java_script_enabled": True,
        "ignore_https_errors": False,
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
        },
    }


# Injected into every page to hide common automation signals
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters && parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters)
);
"""


async def apply_stealth(context) -> None:
    """Attach stealth init script to a Playwright browser context."""
    await context.add_init_script(STEALTH_INIT_SCRIPT)
