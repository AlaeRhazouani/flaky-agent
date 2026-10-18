"""Automatic flaky / not-flaky labels derived from rerun history.

A job is flaky if it both failed and succeeded across the attempts of the same run, i.e. on
the same commit (Ge & Zhang, 2026). Cause and action labels are added by hand later.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import groupby

from flaky_agent.data.collector import FAILED
from flaky_agent.data.storage import Store

FLAKY = "flaky"
NOT_FLAKY = "not_flaky"  # failed on every attempt, and was rerun at least once
UNKNOWN = "unknown"  # failed once and was never rerun: needs our own reruns to decide


@dataclass(frozen=True)
class FailedJob:
    repo: str
    run_id: int
    name: str
    job_id: int  # the first failed attempt: the one the agent diagnoses
    attempts: int
    label: str


def label_sequence(conclusions: list[str]) -> str | None:
    """Label one job from its conclusions across attempts; None if it never failed.

    Cancelled or skipped attempts say nothing about flakiness and are ignored.
    """
    outcomes = [c for c in conclusions if c in FAILED or c == "success"]
    if not any(c in FAILED for c in outcomes):
        return None
    if "success" in outcomes:
        return FLAKY
    return NOT_FLAKY if len(outcomes) > 1 else UNKNOWN


def failed_jobs(store: Store) -> list[FailedJob]:
    result = []
    rows = store.jobs_by_attempt()
    for (repo, run_id, name), group in groupby(rows, key=lambda r: (r[0], r[1], r[2])):
        attempts = list(group)
        conclusions = [r["conclusion"] or "" for r in attempts]
        label = label_sequence(conclusions)
        if label is None:
            continue
        first_failed = next(r["job_id"] for r in attempts if r["conclusion"] in FAILED)
        result.append(FailedJob(repo, run_id, name, first_failed, len(attempts), label))
    return result


def summary(jobs: list[FailedJob]) -> Counter[str]:
    return Counter(job.label for job in jobs)
