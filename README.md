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

## References

- Ge, W., & Zhang, C. (2026). *Understanding and Detecting Flaky Builds in GitHub Actions*. arXiv:2602.02307.
- Project context and decisions so far: [`docs/projet-expertise-flaky-agent-context.md`](docs/projet-expertise-flaky-agent-context.md)
