"""Read collected failed jobs, for manual labeling.

    flaky-show list --label flaky --sample 5   pick jobs to label
    flaky-show job 123456789                   failed steps and error lines of one job

A job's first error is usually the symptom to label; later errors tend to be its consequences.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from flaky_agent.data.labels import failed_jobs
from flaky_agent.data.storage import Store

ERROR_RE = re.compile(
    r"##\[error\]|\[ ?ERROR ?\]|BUILD FAILURE|FAILURE: Build failed|What went wrong"
    r"|Caused by:|\w(Exception|Error):|\b[Ee]rror:|<<< FAILURE!|\bFAILED\b"
)
TIMESTAMP_RE = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\S+Z ")


def error_lines(log: str, limit: int) -> list[tuple[int, str]]:
    """The first `limit` lines that look like errors, numbered from 1, without timestamps."""
    found = []
    for n, line in enumerate(log.splitlines(), 1):
        line = TIMESTAMP_RE.sub("", line)
        if ERROR_RE.search(line):
            found.append((n, line))
            if len(found) == limit:
                break
    return found


def failed_steps(raw_job: str) -> list[str]:
    return [s["name"] for s in json.loads(raw_job).get("steps", []) if s["conclusion"] == "failure"]


def list_jobs(store: Store, args: argparse.Namespace) -> None:
    jobs = [
        j
        for j in failed_jobs(store)
        if (not args.label or j.label == args.label)
        and (not args.repo or j.repo == args.repo)
        and store.log_path(j.repo, j.job_id).exists()
    ]
    if args.sample:
        # One job per project: otherwise the busiest projects fill the whole sample.
        rng = random.Random(args.seed)
        by_repo = defaultdict(list)
        for j in jobs:
            by_repo[j.repo].append(j)
        repos = rng.sample(sorted(by_repo), min(args.sample, len(by_repo)))
        jobs = [rng.choice(by_repo[r]) for r in repos]
    for j in jobs:
        print(f"{j.job_id}  {j.label:<9}  {j.repo}  [{j.name}]  {j.attempts} attempt(s)")
    print(f"{len(jobs)} job(s)", file=sys.stderr)


def show_job(store: Store, args: argparse.Namespace) -> None:
    job = store.job(args.job_id)
    if job is None:
        sys.exit(f"job {args.job_id} is not in the dataset")
    print(f"{job['repo']}  run {job['run_id']} attempt {job['run_attempt']}  [{job['name']}]")
    print(f"https://github.com/{job['repo']}/actions/runs/{job['run_id']}/job/{job['job_id']}")
    print("failed steps:", ", ".join(failed_steps(job["raw"])) or "none recorded")
    if not store.log_path(job["repo"], job["job_id"]).exists():
        sys.exit("no log on disk (expired before it was collected)")
    log = store.read_log(job["repo"], job["job_id"]).decode("utf-8", "replace")
    lines = log.splitlines()
    found = error_lines(log, args.max)
    if found:
        print(f"first error lines (of {len(lines)} log lines):")
    else:
        # Unknown error format: the end of a log usually holds the error.
        print(f"no line matched a known error format; last {args.max} of {len(lines)} lines:")
        start = max(len(lines) - args.max, 0)
        found = [(n, TIMESTAMP_RE.sub("", line)) for n, line in enumerate(lines[start:], start + 1)]
    for n, line in found:
        print(f"{n:>7}  {line[:300]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read collected failed jobs.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", help="list failed jobs that have a log")
    ls.add_argument("--label", choices=["flaky", "not_flaky", "unknown"])
    ls.add_argument("--repo", help="owner/repo")
    ls.add_argument("--sample", type=int, help="draw this many at random, one per project")
    ls.add_argument("--seed", type=int, default=0)
    one = sub.add_parser("job", help="show one job's failed steps and error lines")
    one.add_argument("job_id", type=int)
    one.add_argument("--max", type=int, default=30, help="error lines to print")
    args = parser.parse_args(argv)

    store = Store(args.data_dir)
    try:
        (list_jobs if args.command == "list" else show_job)(store, args)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
