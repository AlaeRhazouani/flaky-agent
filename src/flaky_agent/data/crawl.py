"""Command line entry point: `flaky-crawl --projects config/projects.txt`.

Meant to run once a day (Windows Task Scheduler or cron). GitHub keeps logs for about
90 days, so a missed day is only lost if the gap exceeds the look-back window.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from flaky_agent.data.collector import Collector
from flaky_agent.data.github import GitHubClient, NotFound
from flaky_agent.data.labels import failed_jobs, summary
from flaky_agent.data.storage import Store

log = logging.getLogger("flaky_agent.crawl")


def read_projects(path: Path) -> list[str]:
    """One `owner/repo` per line; blank lines and `#` comments are ignored."""
    projects = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            projects.append(line)
    return projects


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect failed GitHub Actions jobs.")
    parser.add_argument("--projects", type=Path, required=True, help="file of owner/repo lines")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="look-back window in days; reruns of runs in this window are picked up "
        "(use 90 on the first crawl to get everything GitHub still keeps)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request at INFO, including signed log-download URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("GITHUB_TOKEN is not set (see .env.example)")
        return 1

    until = datetime.now(UTC).date()
    since = until - timedelta(days=args.days - 1)
    store = Store(args.data_dir)
    with GitHubClient(token) as client:
        collector = Collector(client, store)
        for repo in read_projects(args.projects):
            log.info("%s: crawling %s to %s", repo, since, until)
            try:
                collector.crawl_repo(repo, since, until)
            except NotFound:
                log.warning("%s: repository not found or Actions disabled, skipped", repo)

    counts = summary(failed_jobs(store))
    log.info("failed jobs so far: %s", dict(counts) or "none")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
