# flaky-agent

LLM agent that diagnoses failed GitHub Actions jobs **without prior training on the project**. For each failed job, it reads the execution log, the commit diff and the job history, then outputs:

1. a decision: flaky or not;
2. the cause category (network, dependency, cache, API limit, memory…);
3. the recommended action, with a justification.

The agent is evaluated against FlakeDetector (Ge & Zhang, 2026) on projects it has never seen.

Projet d'expertise **PE_5A_11**, ENSAM Meknès (GI-ILSI). Team: Alae Rhazouani, Ismail Allouch, Omar Bouhlal. Supervisor: H. Ben-Azza.

## Research questions

| RQ | Question |
|---|---|
| RQ1 | On projects without history, does the agent detect flaky failures better than FlakeDetector? |
| RQ2 | Does the agent correctly identify the cause of the failure? |
| RQ3 | Does the recommended action match the one applied in practice? |
| RQ4 | Is the agent's cost offset by the reruns it avoids? |

## Repository layout

```
src/flaky_agent/
  data/         Block 1: GitHub crawler, dataset building, labeling
  baseline/     Block 2: FlakeDetector, rerun in the cold-start setting
  engine/       Block 3: the agent (log reducer, tools, prompts, output schema)
  evaluation/   Block 4: harness and metrics for RQ1–RQ4, cost model
action/         GitHub Action wrapper around the engine (demo integration)
tests/
data/           Local datasets and logs (not committed)
docs/           Project context and notes
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # create .venv and install dev tools
cp .env.example .env    # then fill in GITHUB_TOKEN and LLM_API_KEY
uv run pytest
uv run ruff check .
```

## Crawler (Block 1)

Collects failed GitHub Actions jobs with their log, commit diff and run history, following the method of Ge & Zhang (2026).

```bash
uv run flaky-select                                          # writes config/projects.txt
uv run flaky-crawl --projects config/projects.txt --days 90   # first crawl
uv run flaky-crawl --projects config/projects.txt              # daily, 30-day look-back
```

- **Project selection** (`flaky-select`): SEART GitHub Search with the paper's criteria (Java, not a fork, ≥150 stars, ≥250 commits, a commit in the last 90 days), then our own step: keep projects with 50–1,000 workflow runs, ≥5 failed runs and ≤50% failed runs in the last 30 days, then draw 100 at random (seed 42). The upper bound keeps the crawl within the API quota and avoids giant monorepos; the failure-rate bound excludes permanently broken workflows. The list notes which projects use secrets (their jobs can't be rerun from a fork). Measurements are cached in `data/selection/candidates.csv`, so an interrupted selection resumes.
- Every completed run is indexed in `data/github.sqlite` (the job history). For runs that failed or were rerun, the jobs of every attempt are fetched. Logs are downloaded for failed jobs only (`data/logs/`), along with the commit (`data/commits/`).
- It is incremental: a run is re-examined when someone reruns it, and nothing is downloaded twice. GitHub keeps logs for about 90 days, so run it **every day** (Windows Task Scheduler or cron).
- Automatic labels (`flaky_agent.data.labels`): **flaky** if the job failed and succeeded across attempts of the same run, **not_flaky** if it failed on every attempt (at least 2), **unknown** if it failed once and was never rerun. Cause and action labels are added by hand.
- Reruns caused by maintainer approval (first-time contributors' PRs) have no jobs in attempt 1, so they are not counted as flaky.

## References

- Ge, W., & Zhang, C. (2026). *Understanding and Detecting Flaky Builds in GitHub Actions*. arXiv:2602.02307.
- Project context and decisions so far: [`docs/projet-expertise-flaky-agent-context.md`](docs/projet-expertise-flaky-agent-context.md)
