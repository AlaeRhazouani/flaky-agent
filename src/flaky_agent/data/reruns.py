"""Rerun numbers for RQ4: how long developers waited to rerun, and how many reruns it took.

flaky-reruns                     for the jobs of labels/sample.csv, into labels/reruns.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from flaky_agent.data.collector import FAILED
from flaky_agent.data.storage import Store


def rerun_stats(attempts: list[dict]) -> dict:
    """attempts: the raw JSON of one job's attempts, oldest first."""
    kept = [a for a in attempts if a.get("conclusion") in FAILED | {"success"}]
    first = next(i for i, a in enumerate(kept) if a["conclusion"] in FAILED)
    after = kept[first + 1 :]

    if after:  # an unknown job was never rerun: no delay
        failed_end = datetime.fromisoformat(kept[first]["completed_at"])
        rerun_created = datetime.fromisoformat(after[0]["created_at"])
        delay = (rerun_created - failed_end).total_seconds()
    else:
        delay = None

    return {
        "delay_seconds": delay,
        "total_reruns": len(after),
        # Counted from 1: a job that passes on its first rerun gives 1. None if it never passed.
        "reruns_before_success": next(
            (n for n, a in enumerate(after, 1) if a["conclusion"] == "success"), None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute rerun numbers for RQ4.")
    parser.add_argument("--jobs", type=Path, default=Path("labels/sample.csv"), help="has job_id")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("labels/reruns.csv"))
    args = parser.parse_args(argv)

    with open(args.jobs, newline="", encoding="utf-8") as f:
        job_ids = [int(row["job_id"]) for row in csv.DictReader(f)]
    store = Store(args.data_dir)
    try:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            columns = ["job_id", "delay_seconds", "total_reruns", "reruns_before_success"]
            w = csv.DictWriter(f, columns)
            w.writeheader()
            for job_id in job_ids:
                job = store.job(job_id)
                stats = rerun_stats(store.attempts(job["repo"], job["run_id"], job["name"]))
                w.writerow({"job_id": job_id, **stats})
    finally:
        store.close()
    print(f"{len(job_ids)} jobs in {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
