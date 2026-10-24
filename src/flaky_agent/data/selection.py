"""Project selection: SEART GitHub Search candidates, filtered by recent GitHub Actions activity.

Stage 1 applies the criteria of Ge & Zhang (2026) through the SEART API: not a fork, main
language Java, at least 150 stars and 250 commits, a commit in the last 3 months.
Stage 2 is ours: for each candidate, count its workflow runs and failed runs over the last
30 days. A project with no runs does not use GitHub Actions, which replaces the paper's
check for a .github/workflows directory. Results are appended to a CSV as they come, so an
interrupted selection resumes where it stopped.
Stage 3 keeps projects with bounded activity and draws a reproducible random sample:
- at least 50 runs and 5 failed runs: enough failures to collect;
- at most 1,000 runs: keeps the crawl within the API quota, and stays far below the
  2,500 ceiling of the runs endpoint's total_count, so the reported counts are exact;
- at most 50% failed runs: excludes permanently broken workflows, which are not flaky.
Ranking by failures instead would select giant monorepos only. Whether the workflows use
secrets is recorded for the drawn projects, since such jobs cannot be rerun from a fork.
"""

from __future__ import annotations

import argparse
import base64
import csv
import logging
import os
import random
import re
import ssl
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import truststore
from dotenv import load_dotenv

from flaky_agent.data.github import GitHubClient, NotFound

SEART_URL = "https://seart-ghs.si.usi.ch/api/r/search"
FIELDS = [
    "repo",
    "stars",
    "commits",
    "contributors",
    "last_commit",
    "status",  # 'ok' or 'not_found'
    "runs_30d",
    "failed_runs_30d",
]
# Any secret other than the built-in GITHUB_TOKEN, or secrets passed on to reusable workflows.
SECRET_RE = re.compile(r"secrets\.(?!GITHUB_TOKEN\b)\w+|secrets:\s*inherit")

log = logging.getLogger("flaky_agent.select")


def seart_candidates(
    language: str, stars_min: int, commits_min: int, committed_since: str
) -> Iterator[dict[str, Any]]:
    params: dict[str, Any] = {
        "language": language,
        "starsMin": stars_min,
        "commitsMin": commits_min,
        "committedMin": committed_since,
        "excludeForks": "true",
        "size": 100,
    }
    # SEART's server omits its intermediate certificate; the OS trust store can fetch it,
    # Python's bundled one cannot. Verification stays on.
    tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with httpx.Client(timeout=120, verify=tls) as http:
        page = 0
        while True:
            body = http.get(SEART_URL, params={**params, "page": page}).raise_for_status().json()
            for item in body["items"]:
                if not item.get("isArchived"):
                    yield item
            if body["last"]:
                return
            page += 1
            time.sleep(0.5)  # be polite to a free academic service


def count_runs(client: GitHubClient, repo: str, since: str, status: str | None = None) -> int:
    params = {"created": f">={since}", "per_page": 1}
    if status:
        params["status"] = status
    return client.get_json(f"/repos/{repo}/actions/runs", params)["total_count"]


def uses_secrets(client: GitHubClient, repo: str) -> bool:
    try:
        files = client.get_json(f"/repos/{repo}/contents/.github/workflows")
    except NotFound:
        return False
    for f in files:
        if f["type"] != "file" or not f["name"].endswith((".yml", ".yaml")):
            continue
        content = base64.b64decode(client.get_json(f["url"])["content"]).decode("utf-8", "replace")
        if SECRET_RE.search(content):
            return True
    return False


def measure(client: GitHubClient, item: dict[str, Any], since: str) -> dict[str, Any]:
    row = {
        "repo": item["name"],
        "stars": item["stargazers"],
        "commits": item["commits"],
        "contributors": item["contributors"],
        "last_commit": item["lastCommit"],
    }
    try:
        row["runs_30d"] = count_runs(client, item["name"], since)
        row["failed_runs_30d"] = count_runs(client, item["name"], since, status="failure")
        row["status"] = "ok"
    except NotFound:
        row.update(status="not_found", runs_30d=0, failed_runs_30d=0)
    return row


def read_measured(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["repo"]: row for row in csv.DictReader(f)}


def eligible(
    rows: Iterable[dict[str, str]],
    min_runs: int,
    max_runs: int,
    min_failed: int,
    max_failed_ratio: float,
) -> list[dict[str, str]]:
    """Measured candidates within the activity bounds, sorted by name for a stable draw."""
    kept = []
    for r in rows:
        if r["status"] != "ok":
            continue
        runs, failed = int(r["runs_30d"]), int(r["failed_runs_30d"])
        if (
            min_runs <= runs <= max_runs
            and failed >= min_failed
            and failed <= max_failed_ratio * runs
        ):
            kept.append(r)
    return sorted(kept, key=lambda r: r["repo"].lower())


def draw(pool: list[dict[str, str]], size: int, seed: int) -> list[dict[str, str]]:
    sample = random.Random(seed).sample(pool, min(size, len(pool)))
    return sorted(sample, key=lambda r: r["repo"].lower())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select projects to crawl.")
    parser.add_argument("--language", default="Java")
    parser.add_argument("--stars-min", type=int, default=150)
    parser.add_argument("--commits-min", type=int, default=250)
    parser.add_argument("--min-runs", type=int, default=50, help="workflow runs in 30 days")
    parser.add_argument("--max-runs", type=int, default=1000, help="workflow runs in 30 days")
    parser.add_argument("--min-failed", type=int, default=5, help="failed runs in 30 days")
    parser.add_argument("--max-failed-ratio", type=float, default=0.5)
    parser.add_argument("--sample", type=int, default=100, help="number of projects to draw")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--limit", type=int, help="measure at most this many new candidates (for a trial run)"
    )
    parser.add_argument("--candidates", type=Path, default=Path("data/selection/candidates.csv"))
    parser.add_argument("--output", type=Path, default=Path("config/projects.txt"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("GITHUB_TOKEN is not set (see .env.example)")
        return 1

    today = datetime.now(UTC).date()
    committed_since = (today - timedelta(days=90)).isoformat()
    runs_since = (today - timedelta(days=30)).isoformat()

    measured = read_measured(args.candidates)
    args.candidates.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.candidates.exists()
    with (
        GitHubClient(token) as client,
        args.candidates.open("a", newline="", encoding="utf-8") as f,
    ):
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        items = seart_candidates(args.language, args.stars_min, args.commits_min, committed_since)
        todo = (item for item in items if item["name"] not in measured)
        for n, item in enumerate(todo, 1):
            if args.limit and n > args.limit:
                break
            row = measure(client, item, runs_since)
            writer.writerow(row)
            f.flush()
            measured[row["repo"]] = {k: str(v) for k, v in row.items()}
            if n % 50 == 0:
                log.info("measured %d new candidates", n)

        pool = eligible(
            measured.values(), args.min_runs, args.max_runs, args.min_failed, args.max_failed_ratio
        )
        selected = draw(pool, args.sample, args.seed)
        log.info(
            "%d candidates measured, %d eligible, %d drawn", len(measured), len(pool), len(selected)
        )
        secrets = {r["repo"]: uses_secrets(client, r["repo"]) for r in selected}

    write_projects(args, len(measured), len(pool), selected, secrets, today.isoformat())
    log.info("wrote %s", args.output)
    return 0


def write_projects(
    args: argparse.Namespace,
    n_measured: int,
    n_eligible: int,
    selected: list[dict[str, str]],
    secrets: dict[str, bool],
    today: str,
) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Selected on {today} by flaky-select.",
        f"# SEART: language={args.language}, stars>={args.stars_min}, "
        f"commits>={args.commits_min}, no forks, committed in the last 90 days "
        f"({n_measured} candidates).",
        f"# Eligible: {args.min_runs}<=runs_30d<={args.max_runs}, "
        f"failed_runs_30d>={args.min_failed}, failed ratio<={args.max_failed_ratio} "
        f"({n_eligible} projects).",
        f"# Drawn: {len(selected)} at random, seed {args.seed}.",
        "# Columns after the repo are informational: failed runs / runs in 30 days, secrets.",
        "",
    ]
    for r in selected:
        flag = "uses-secrets" if secrets[r["repo"]] else "no-secrets"
        lines.append(f"{r['repo']:<50} # {r['failed_runs_30d']}/{r['runs_30d']} {flag}")
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
