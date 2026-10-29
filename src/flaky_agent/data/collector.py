"""Collects failed jobs from GitHub Actions: runs, jobs per attempt, logs and commit diffs.

Follows the method of Ge & Zhang (2026): every completed run is indexed (this is the job
history), and for runs that failed or were rerun, every attempt's jobs are fetched so that
flaky jobs can be identified later (see labels.py). Logs are downloaded for failed jobs only.

The crawl is incremental and safe to interrupt: a run is re-examined when its run_attempt
grows (someone reran it), and nothing already collected is downloaded twice.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta

from flaky_agent.data.github import GitHubClient, NotFound
from flaky_agent.data.storage import Store

# Job conclusions treated as failures. timed_out is included because hanging jobs are a
# common flaky symptom.
FAILED = {"failure", "timed_out"}

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, client: GitHubClient, store: Store) -> None:
        self.client = client
        self.store = store

    def crawl_repo(self, repo: str, since: date, until: date) -> None:
        self.index_runs(repo, since, until)
        for run in self.store.pending_runs(repo, FAILED):
            try:
                self.process_run(repo, run)
            except Exception:
                # Left unprocessed, so the next crawl retries it.
                log.exception("%s: failed to process run %s", repo, run["run_id"])

    def index_runs(self, repo: str, since: date, until: date) -> None:
        """Store every completed run created between `since` and `until` (inclusive).

        Queried one day at a time because the runs endpoint caps filtered results at 1,000.
        """
        count = 0
        for day in _days(since, until):
            runs = self.client.paginate(
                f"/repos/{repo}/actions/runs",
                {"created": day.isoformat(), "status": "completed"},
                key="workflow_runs",
            )
            for run in runs:
                self.store.upsert_run(repo, run)
                count += 1
        log.info("%s: indexed %d runs", repo, count)

    def process_run(self, repo: str, run: sqlite3.Row) -> None:
        run_id = run["run_id"]
        any_failed = False
        for attempt in range(run["processed_attempt"] + 1, run["run_attempt"] + 1):
            # Pages of 100 jobs (each with its steps) time out with a 502 on large matrix runs.
            jobs = self.client.paginate(
                f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs",
                {"per_page": 30},
                key="jobs",
            )
            for job in jobs:
                log_status = None
                if job.get("conclusion") in FAILED:
                    any_failed = True
                    log_status = self.fetch_log(repo, job["id"])
                self.store.upsert_job(repo, job, log_status)
        if any_failed and run["head_sha"]:
            self.fetch_commit(repo, run["head_sha"])
        self.store.mark_processed(repo, run_id, run["run_attempt"])

    def fetch_log(self, repo: str, job_id: int) -> str:
        if self.store.log_path(repo, job_id).exists():
            return "ok"
        try:
            content = self.client.get_bytes(f"/repos/{repo}/actions/jobs/{job_id}/logs")
        except NotFound:
            return "expired"
        self.store.save_log(repo, job_id, content)
        return "ok"

    def fetch_commit(self, repo: str, sha: str) -> None:
        if self.store.has_commit(repo, sha):
            return
        try:
            commit = self.client.get_json(f"/repos/{repo}/commits/{sha}")
        except NotFound:
            commit = None  # e.g. force-pushed away
        self.store.save_commit(repo, sha, commit)


def _days(since: date, until: date) -> Iterator[date]:
    day = since
    while day <= until:
        yield day
        day += timedelta(days=1)
