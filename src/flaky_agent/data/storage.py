"""Crawler storage: a SQLite index of runs and jobs, plus raw logs and commits on disk.

Layout under the data directory:
    github.sqlite                      runs, jobs, commits (with the raw API JSON)
    logs/{owner}__{repo}/{job_id}.log  one log per failed job
    commits/{owner}__{repo}/{sha}.json commit metadata and diff
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    repo              TEXT NOT NULL,
    run_id            INTEGER NOT NULL,
    workflow_id       INTEGER,
    workflow_name     TEXT,
    event             TEXT,
    head_sha          TEXT,
    head_branch       TEXT,
    run_attempt       INTEGER NOT NULL,
    conclusion        TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    raw               TEXT NOT NULL,
    -- highest attempt whose jobs, logs and commit have been collected
    processed_attempt INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, run_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id       INTEGER PRIMARY KEY,
    repo         TEXT NOT NULL,
    run_id       INTEGER NOT NULL,
    run_attempt  INTEGER NOT NULL,
    name         TEXT NOT NULL,
    conclusion   TEXT,
    started_at   TEXT,
    completed_at TEXT,
    raw          TEXT NOT NULL,
    -- 'ok' or 'expired' for failed jobs, NULL when no log is needed
    log_status   TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_run ON jobs (repo, run_id, name, run_attempt);
CREATE TABLE IF NOT EXISTS commits (
    repo   TEXT NOT NULL,
    sha    TEXT NOT NULL,
    status TEXT NOT NULL,  -- 'ok' or 'missing'
    PRIMARY KEY (repo, sha)
);
"""


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(data_dir / "github.sqlite")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert_run(self, repo: str, run: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (repo, run_id, workflow_id, workflow_name, event, head_sha,
                              head_branch, run_attempt, conclusion, created_at, updated_at, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (repo, run_id) DO UPDATE SET
                run_attempt = excluded.run_attempt,
                conclusion  = excluded.conclusion,
                updated_at  = excluded.updated_at,
                raw         = excluded.raw
            """,
            (
                repo,
                run["id"],
                run.get("workflow_id"),
                run.get("name"),
                run.get("event"),
                run.get("head_sha"),
                run.get("head_branch"),
                run.get("run_attempt", 1),
                run.get("conclusion"),
                run.get("created_at"),
                run.get("updated_at"),
                json.dumps(run),
            ),
        )
        self.conn.commit()

    def pending_runs(self, repo: str, failed: set[str]) -> list[sqlite3.Row]:
        """Runs with unprocessed attempts that may contain a failed job."""
        marks = ",".join("?" * len(failed))
        return self.conn.execute(
            f"""
            SELECT * FROM runs
            WHERE repo = ? AND run_attempt > processed_attempt
              AND (conclusion IN ({marks}) OR run_attempt > 1)
            ORDER BY run_id
            """,
            (repo, *sorted(failed)),
        ).fetchall()

    def mark_processed(self, repo: str, run_id: int, attempt: int) -> None:
        self.conn.execute(
            "UPDATE runs SET processed_attempt = ? WHERE repo = ? AND run_id = ?",
            (attempt, repo, run_id),
        )
        self.conn.commit()

    def upsert_job(self, repo: str, job: dict[str, Any], log_status: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO jobs (job_id, repo, run_id, run_attempt, name, conclusion,
                              started_at, completed_at, raw, log_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (job_id) DO UPDATE SET
                conclusion = excluded.conclusion,
                raw        = excluded.raw,
                log_status = COALESCE(excluded.log_status, jobs.log_status)
            """,
            (
                job["id"],
                repo,
                job["run_id"],
                job.get("run_attempt", 1),
                job["name"],
                job.get("conclusion"),
                job.get("started_at"),
                job.get("completed_at"),
                json.dumps(job),
                log_status,
            ),
        )
        self.conn.commit()

    def log_path(self, repo: str, job_id: int) -> Path:
        return self.data_dir / "logs" / _slug(repo) / f"{job_id}.log"

    def save_log(self, repo: str, job_id: int, content: bytes) -> None:
        path = self.log_path(repo, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def has_commit(self, repo: str, sha: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM commits WHERE repo = ? AND sha = ?", (repo, sha)
        ).fetchone()
        return row is not None

    def save_commit(self, repo: str, sha: str, commit: dict[str, Any] | None) -> None:
        """Save a commit's JSON, or record it as missing when `commit` is None."""
        if commit is not None:
            path = self.data_dir / "commits" / _slug(repo) / f"{sha}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(commit), encoding="utf-8")
        self.conn.execute(
            "INSERT OR REPLACE INTO commits (repo, sha, status) VALUES (?, ?, ?)",
            (repo, sha, "ok" if commit is not None else "missing"),
        )
        self.conn.commit()

    def jobs_by_attempt(self) -> list[sqlite3.Row]:
        """All jobs, ordered so that each (repo, run, job name) lists its attempts in order."""
        return self.conn.execute(
            """
            SELECT repo, run_id, name, run_attempt, job_id, conclusion FROM jobs
            ORDER BY repo, run_id, name, run_attempt
            """
        ).fetchall()


def _slug(repo: str) -> str:
    return repo.replace("/", "__")
