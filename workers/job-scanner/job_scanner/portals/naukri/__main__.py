"""CLI: python -m job_scanner.portals.naukri --keyword "python developer" --location bangalore"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from job_scanner.portals.naukri import NaukriScraper


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Naukri.com job search results")
    parser.add_argument("--keyword", "-k", required=True, help='e.g. "python developer"')
    parser.add_argument("--location", "-l", default=None, help='e.g. "bangalore"')
    parser.add_argument("--pages", "-p", type=int, default=1, help="Max SERP pages (default 1)")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless (may be blocked by Naukri/Akamai)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON instead of a short table",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    async with NaukriScraper(
        keyword=args.keyword,
        location=args.location,
        max_pages=args.pages,
        headless=args.headless,
    ) as scraper:
        jobs = await scraper.scrape()
        search_url = scraper.search_url

    if args.json:
        print(json.dumps([j.to_dict() for j in jobs], indent=2, ensure_ascii=False))
    else:
        print(f"Found {len(jobs)} jobs from Naukri")
        print(f"Search URL: {search_url}")
        for i, job in enumerate(jobs, start=1):
            loc = job.location or "-"
            print(f"{i:3}. {job.title} @ {job.company} [{loc}]")
            print(f"     {job.url}")

    return 0 if jobs else 1


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main(sys.argv[1:])
