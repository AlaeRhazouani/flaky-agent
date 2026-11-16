"""Draw the jobs to label by hand, from the test projects (see docs/labeling-guide.md).

    flaky-sample --annotators alae ismail

Every flaky job is taken up to a cap per project, so that one busy project can't fill the sample;
not_flaky jobs are drawn under the same cap. Some jobs go to both annotators, to compute kappa;
the rest are split between them.
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import sys
import zipfile
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

from flaky_agent.data.crawl import read_projects
from flaky_agent.data.labels import FLAKY, NOT_FLAKY, FailedJob, failed_jobs
from flaky_agent.data.show import show_job
from flaky_agent.data.storage import Store

LABEL_COLUMNS = ["job_id", "repo", "category", "root_cause", "action", "uncertain", "note"]


def capped(jobs: list[FailedJob], cap: int, rng: random.Random) -> list[FailedJob]:
    """At most `cap` jobs per project, drawn at random."""
    by_repo = defaultdict(list)
    for j in sorted(jobs, key=lambda j: j.job_id):
        by_repo[j.repo].append(j)
    return [j for r in sorted(by_repo) for j in rng.sample(by_repo[r], min(cap, len(by_repo[r])))]


def draw(
    jobs: list[FailedJob], cap: int, not_flaky: int, overlap: int, seed: int
) -> tuple[list[FailedJob], set[int]]:
    """The sample, and the job ids both annotators label."""
    rng = random.Random(seed)
    sample = capped([j for j in jobs if j.label == FLAKY], cap, rng)
    pool = capped([j for j in jobs if j.label == NOT_FLAKY], cap, rng)
    sample += rng.sample(pool, min(not_flaky, len(pool)))
    rng.shuffle(sample)  # annotators shouldn't see the jobs grouped by project or label
    return sample, {j.job_id for j in rng.sample(sample, min(overlap, len(sample)))}


def export_logs(store: Store, sample: list[FailedJob], path: Path) -> None:
    """One `{job_id}.txt` per job: the flaky-show summary, then the full log.

    For an annotator without the crawled data; GitHub deletes logs after about 90 days.
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for j in sample:
            summary = io.StringIO()
            with redirect_stdout(summary):
                show_job(store, argparse.Namespace(job_id=j.job_id, max=30))
            log = store.read_log(j.repo, j.job_id).decode("utf-8", "replace")
            z.writestr(f"{j.job_id}.txt", f"{summary.getvalue()}\n----- full log -----\n{log}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the jobs to label by hand.")
    parser.add_argument("--annotators", nargs=2, required=True, metavar="NAME")
    parser.add_argument("--projects", type=Path, default=Path("config/test-projects.txt"))
    parser.add_argument("--cap", type=int, default=10, help="jobs per project and label")
    parser.add_argument("--not-flaky", type=int, default=50)
    parser.add_argument("--overlap", type=int, default=50, help="jobs labeled by both annotators")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("labels"))
    parser.add_argument("--zip", action="store_true", help="also zip the sample's logs")
    args = parser.parse_args(argv)

    test = set(read_projects(args.projects))
    store = Store(args.data_dir)
    try:
        jobs = [
            j
            for j in failed_jobs(store)
            if j.repo in test and store.log_path(j.repo, j.job_id).exists()
        ]
        sample, both = draw(jobs, args.cap, args.not_flaky, args.overlap, args.seed)
        args.output.mkdir(parents=True, exist_ok=True)
        if args.zip:
            export_logs(store, sample, args.output / "logs.zip")
    finally:
        store.close()

    with open(args.output / "sample.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["job_id", "repo", "label", "annotators"])
        rest = [j for j in sample if j.job_id not in both]
        owner = {j.job_id: args.annotators[i % 2] for i, j in enumerate(rest)}
        for j in sample:
            w.writerow([j.job_id, j.repo, j.label, "both" if j.job_id in both else owner[j.job_id]])
    for name in args.annotators:
        with open(args.output / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(LABEL_COLUMNS)
            for j in sample:
                if j.job_id in both or owner[j.job_id] == name:
                    w.writerow([j.job_id, j.repo] + [""] * (len(LABEL_COLUMNS) - 2))
    print(f"{len(sample)} jobs, {len(both)} labeled by both, in {args.output}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
