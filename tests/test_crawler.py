from datetime import date

import httpx
import pytest

from flaky_agent.data.collector import Collector
from flaky_agent.data.github import GitHubClient
from flaky_agent.data.labels import FLAKY, NOT_FLAKY, UNKNOWN, failed_jobs, label_sequence
from flaky_agent.data.selection import SECRET_RE, draw, eligible
from flaky_agent.data.storage import Store

REPO = "acme/app"


@pytest.mark.parametrize(
    ("conclusions", "expected"),
    [
        (["failure", "success"], FLAKY),
        (["timed_out", "failure", "success"], FLAKY),
        (["failure", "failure"], NOT_FLAKY),
        (["failure"], UNKNOWN),
        (["failure", "cancelled"], UNKNOWN),
        (["success"], None),
        (["cancelled"], None),
    ],
)
def test_label_sequence(conclusions, expected):
    assert label_sequence(conclusions) == expected


def run(run_id, attempt, conclusion, sha="abc123"):
    return {
        "id": run_id,
        "workflow_id": 7,
        "name": "CI",
        "event": "push",
        "head_sha": sha,
        "head_branch": "main",
        "run_attempt": attempt,
        "conclusion": conclusion,
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-01T10:30:00Z",
    }


def job(job_id, run_id, attempt, name, conclusion):
    return {
        "id": job_id,
        "run_id": run_id,
        "run_attempt": attempt,
        "name": name,
        "conclusion": conclusion,
    }


class FakeGitHub:
    """Serves a tiny fake Actions API and records every requested path."""

    def __init__(self):
        self.runs = []
        self.jobs = {}  # (run_id, attempt) -> list of jobs
        self.expired_logs = set()
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        parts = path.strip("/").split("/")
        if path.endswith("/actions/runs"):
            day = request.url.params["created"]
            runs = [r for r in self.runs if r["created_at"].startswith(day)]
            return httpx.Response(200, json={"workflow_runs": runs})
        if "attempts" in parts:
            run_id, attempt = int(parts[5]), int(parts[7])
            return httpx.Response(200, json={"jobs": self.jobs.get((run_id, attempt), [])})
        if path.endswith("/logs"):
            job_id = int(parts[5])
            if job_id in self.expired_logs:
                return httpx.Response(410)
            return httpx.Response(200, content=f"log of {job_id}".encode())
        if "commits" in parts:
            return httpx.Response(200, json={"sha": parts[-1], "files": []})
        return httpx.Response(404)


@pytest.fixture
def setup(tmp_path):
    fake = FakeGitHub()
    client = GitHubClient("token", transport=httpx.MockTransport(fake.handler))
    store = Store(tmp_path)
    yield fake, Collector(client, store), store
    client.close()
    store.close()


DAY = date(2026, 10, 1)


def test_collects_failed_jobs_logs_and_commit(setup):
    fake, collector, store = setup
    fake.runs = [run(1, 2, "success"), run(2, 1, "success"), run(3, 1, "failure", sha="def456")]
    fake.jobs = {
        (1, 1): [job(10, 1, 1, "build", "failure"), job(11, 1, 1, "lint", "success")],
        (1, 2): [job(12, 1, 2, "build", "success")],
        (3, 1): [job(30, 3, 1, "build", "failure")],
    }
    fake.expired_logs = {30}

    collector.crawl_repo(REPO, DAY, DAY)

    labels = {(j.run_id, j.name): (j.label, j.job_id) for j in failed_jobs(store)}
    assert labels == {(1, "build"): (FLAKY, 10), (3, "build"): (UNKNOWN, 30)}
    assert store.read_log(REPO, 10) == b"log of 10"
    assert not store.log_path(REPO, 30).exists()
    assert store.has_commit(REPO, "abc123") and store.has_commit(REPO, "def456")
    # Run 2 succeeded on its only attempt: its jobs are never fetched.
    assert not any("/runs/2/" in p for p in fake.requests)


def test_rerun_after_first_crawl_is_picked_up(setup):
    fake, collector, store = setup
    fake.runs = [run(1, 1, "failure")]
    fake.jobs = {(1, 1): [job(10, 1, 1, "build", "failure")]}
    collector.crawl_repo(REPO, DAY, DAY)
    assert [j.label for j in failed_jobs(store)] == [UNKNOWN]

    # A developer reruns it and it passes; the next crawl sees run_attempt = 2.
    fake.runs = [run(1, 2, "success")]
    fake.jobs[(1, 2)] = [job(12, 1, 2, "build", "success")]
    fake.requests.clear()
    collector.crawl_repo(REPO, DAY, DAY)

    assert [j.label for j in failed_jobs(store)] == [FLAKY]
    # Only the new attempt is fetched, and the existing log is not downloaded again.
    assert not any("/attempts/1/" in p for p in fake.requests)
    assert not any(p.endswith("/logs") for p in fake.requests)


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        ("token: ${{ secrets.GITHUB_TOKEN }}", False),
        ("password: ${{ secrets.SONATYPE_PASSWORD }}", True),
        ("    secrets: inherit", True),
        ("run: mvn verify", False),
    ],
)
def test_secret_detection(workflow, expected):
    assert bool(SECRET_RE.search(workflow)) == expected


def candidate(repo, runs, failed, status="ok"):
    return {"repo": repo, "status": status, "runs_30d": str(runs), "failed_runs_30d": str(failed)}


def test_eligible_applies_activity_bounds():
    rows = [
        candidate("ok/project", 200, 20),
        candidate("too/quiet", 49, 10),
        candidate("too/busy", 1001, 50),
        candidate("few/failures", 200, 4),
        candidate("always/broken", 100, 51),
        candidate("gone/repo", 200, 20, status="not_found"),
    ]
    assert [r["repo"] for r in eligible(rows, 50, 1000, 5, 0.5)] == ["ok/project"]


def test_draw_is_reproducible():
    pool = [candidate(f"org/p{i:03}", 100, 10) for i in range(300)]
    first = draw(pool, 100, seed=42)
    assert len(first) == 100
    assert first == draw(pool, 100, seed=42)
    assert first != draw(pool, 100, seed=7)
