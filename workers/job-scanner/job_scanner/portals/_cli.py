"""Shared CLI bootstrap for portal scrapers."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Callable


def run_portal_cli(
    *,
    scraper_factory: Callable[..., Any],
    description: str,
    portal_label: str,
    argv: list[str] | None = None,
) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--keyword", "-k", required=True)
    parser.add_argument("--location", "-l", default=None)
    parser.add_argument("--pages", "-p", type=int, default=1)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    async def _run() -> int:
        async with scraper_factory(
            keyword=args.keyword,
            location=args.location,
            max_pages=args.pages,
            headless=args.headless,
        ) as scraper:
            jobs = await scraper.scrape()
            search_url = getattr(scraper, "search_url", "")

        if args.json:
            print(json.dumps([j.to_dict() for j in jobs], indent=2, ensure_ascii=False))
        else:
            print(f"Found {len(jobs)} jobs from {portal_label}")
            if search_url:
                print(f"Search URL: {search_url}")
            for i, job in enumerate(jobs, start=1):
                loc = job.location or "-"
                print(f"{i:3}. {job.title} @ {job.company} [{loc}]")
                print(f"     {job.url}")
        return 0 if jobs else 1

    raise SystemExit(asyncio.run(_run()))
