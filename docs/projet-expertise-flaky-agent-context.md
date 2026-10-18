# Projet d'expertise PE_5A_11: LLM Agent for Diagnosing Flaky Failures in GitHub Actions

> Context file: everything about the project and what has been discussed and agreed so far.
> Status as of 2026-10-17. Items marked **[to confirm]** still need the supervisor's validation.

---

## 1. Project identity

| Item | Value |
|---|---|
| Course | Projet d'expertise, 5th year, ENSAM Meknès (GI-ILSI) |
| Reference | PE_5A_11 |
| Supervisor | H. Ben-Azza (co-encadrante) |
| Team | Alae Rhazouani, Ismail Allouch, Omar Bouhlal |
| Official title | *Agent IA de diagnostic des échecs flaky dans les pipelines GitHub Actions* |
| Type | Applied research project: design, implement, **and evaluate** |
| Note | The subject document is headed "Sujet de PFE : 5e année". **[to confirm]** that the scope is adjusted to a projet d'expertise timeline |

The subject replaced an earlier, broader one (governing agent decisions at CI/CD control points). Only the current version below applies.

---

## 2. The subject (summary of the official document)

**Context.** A CI build can fail without the code being at fault: network outage, corrupted cache, saturated external service, API rate limit. This is a **flaky failure**. Faced with a failure, the developer doesn't know whether to fix the code or rerun the pipeline. Most of the time they rerun, sometimes several times.

**Baseline.** FlakeDetector (Ge et al., 2026) predicts whether a failed job is flaky, with an average F1 of 0.80 on 10 Java projects. It has three limits:
1. It is trained and evaluated on each project's own history; its effectiveness on a **new project without history** has not been evaluated.
2. It outputs a probability converted into a binary decision, **without explaining the cause**.
3. It **doesn't indicate the action** to take (rerun, clear the cache, renew a token…).

**Central question.** Can an LLM-based AI agent, investigating a failure like a DevOps engineer would, diagnose it, identify its cause, and recommend the right action, **without prior training on the project**?

**Work requested.** For each failed job, the agent consults the **execution log**, the **commit diff**, and the **job history**, then produces:
1. a decision: flaky or not;
2. the cause category (network, dependency, cache, API limit, memory…);
3. the recommended action, with a justification.

**Research questions.**
- **RQ1:** On projects without history, does the agent detect flaky failures better than FlakeDetector?
- **RQ2:** Does the agent correctly identify the cause of the failure?
- **RQ3:** Does the recommended action match the one applied in practice?
- **RQ4:** Is the agent's cost offset by the reruns it avoids?

---

## 3. Key concepts

### Flaky failure
A failure that happens **without the code being wrong**: the same code, on the same commit, can fail and then pass with no change. The cause is the environment, not the code change.

- **Test:** rerun on the same commit. Passes → flaky. Fails the same way every time → real failure.
- **Why it matters:** wasted CI minutes and developer time; loss of trust ("just rerun" culture), which lets real bugs slip through; slower delivery.

| | Real failure | Flaky failure |
|---|---|---|
| Cause | The code (bug, broken test, syntax error) | The environment (network, cache, external service, rate limit) |
| Rerun on same commit | Fails again, same way | Can pass |
| Right action | Fix the code | Rerun, or fix the environment |

### Typical causes and actions

| Category | Example in a log | Right action |
|---|---|---|
| Network | `Connection reset by peer`, download timeout | Rerun |
| Dependency / external service | Registry returns 503, missing artifact | Rerun later / upload the missing artifact |
| Cache | Corrupted or stale cache restored | Refresh the cache, then rerun |
| API rate limit | `HTTP 403: API rate limit exceeded` | Wait, or use an authenticated token |
| Expired credentials | `401 Unauthorized` | Refresh the token |
| Memory / resources | `Killed` (OOM), disk full | Rerun / bigger runner / clean disk |
| Timing / concurrency | Test passes or fails depending on thread scheduling | Fix the test (rerun hides a real bug) |

### Important nuances
- **Flaky test vs flaky job/build.** A flaky test is one non-deterministic test. A flaky job fails for an environmental reason, often before tests even run. The subject targets the broader job level.
- **Concurrency-flaky tests are a trap:** non-deterministic, but the problem is in the code, so "rerun" is the wrong action. **[to confirm]** whether they count as flaky in our labels.

### Existing solutions (state of the art)
1. **Automatic retry**: simple but blind; reruns real bugs too and hides problems.
2. **Rule-based log matching**: fast and explainable, but only catches known patterns.
3. **Flaky test detection and quarantine** (e.g. Trunk, BuildPulse, Datadog): needs history, test-level only, no cause.
4. **Machine learning (FlakeDetector)**: better accuracy, but the three limits above.
5. **Fixing root causes** (caching, mirrors, pinning, auth, resources): the real fix, but requires diagnosis first.
6. **Our LLM agent**: targets FlakeDetector's three limits; its diagnosis can feed the other layers (rerun only when appropriate, route cache/token issues to someone who can fix them).

Hobby tools already use LLMs to explain flaky CI jobs (e.g. flakehunter). **Our contribution is not the idea of an LLM agent, but its rigorous evaluation against FlakeDetector on unseen projects.**

---

## 4. Project objective (agreed formulation)

**English:**
> Design an LLM-based agent that diagnoses failed GitHub Actions jobs without prior training on the project, deciding whether the failure is flaky, identifying its cause, and recommending a justified action, and evaluate whether it outperforms FlakeDetector and is cost-effective.

**French (for the report):**
> Concevoir un agent IA fondé sur un LLM qui, sans entraînement préalable sur le projet, diagnostique les jobs GitHub Actions en échec : il détermine si l'échec est flaky, identifie sa cause et recommande une action justifiée. Évaluer ensuite s'il surpasse FlakeDetector et si son coût est rentable.

**Wording rules agreed:**
- Say **"action"**, not "fix": the agent recommends actions (rerun, clear cache, renew token), it does **not** generate code patches.
- The objective has two halves: **build** the agent and **prove** it works (comparison + cost).

### Specific objectives

| Specific objective | FlakeDetector limit addressed | RQ |
|---|---|---|
| Detect flaky failures on projects **without history** | Needs per-project training | RQ1 |
| Identify the **cause** of the failure | Binary output only | RQ2 |
| Recommend the right **action**, with a **justification** | No action, no explanation | RQ3 |
| Show the agent is **cost-effective** | New dimension: LLMs cost money | RQ4 |

---

## 5. Agent behavior

### One investigation produces the verdict
The agent does not decide "real or flaky" first and investigate afterwards. It investigates (log, diff, history), and the evidence leads to the verdict.

```
CI job fails
      │
      ▼
Agent investigates (log, diff, history)
      │
      ▼
 Evidence found
   ├── "Connection timeout to registry" → FLAKY → cause: network → action: rerun
   └── "AssertionError in test_login, test file changed in diff" → REAL → fix the code
```

### When the failure is real
The subject only lists actions for flaky cases. Three possible levels:

| Level | Output | Scope |
|---|---|---|
| 1. Minimal | "Real failure → fix the code" | Enough for the RQs |
| 2. Diagnostic | Failure type (test failure / compilation / lint) + likely culprit file/line from the diff + evidence | **Recommended** |
| 3. Repair | Generates a code patch | **Out of scope** (automated program repair is a separate field) |

**[to confirm]** Level 2 with the supervisor (it also extends the cause taxonomy to real-failure categories).

### Asymmetric errors: the agent must be cautious
| Mistake | Consequence |
|---|---|
| Says **real**, but it was flaky | Developer looks for a nonexistent bug. Lost time, but safe. |
| Says **flaky**, but it was real | Developer reruns; a real bug may reach production. **Dangerous.** |

When evidence is unclear, the agent says "real" or "uncertain", never "flaky". In the evaluation, report the rate of real failures wrongly labeled flaky separately, not only F1.

### Reliability caveat
LLMs are non-deterministic (even at temperature 0: GPU arithmetic, batching, model updates) and sensitive to small input changes. Their reliability can be improved but not guaranteed. → Measure **consistency** by running the agent several times on the same failures.

---

## 6. Project structure: the 4 building blocks

This is a **work breakdown** (dependencies between pieces of work), not a system architecture. Arrows mean "is needed by".

```
1. Dataset ──────► 2. Baseline (FlakeDetector)
     │                        │
     └────► 3. Agent ─────────┤
                              ▼
                       4. Evaluation (RQ1–RQ4)
```

The dataset comes first: nothing else can be finished without it.

### Block 1: Dataset
The basic unit is a **failed job**, not a project. Each record:

| Field | Purpose |
|---|---|
| Job log | Main evidence for the agent |
| Commit diff | Did the code change cause it? |
| Job history (past runs, attempts) | Recurring pattern? |
| Label: flaky or not | Ground truth for RQ1 |
| Label: cause category | Ground truth for RQ2 |
| Label: action taken | Ground truth for RQ3 |
| Durations, number of reruns | Data for RQ4 |

**Sources:**
1. **FlakeDetector paper's data (start here).** Paper: *Understanding and Detecting Flaky Builds in GitHub Actions*, Wenhao Ge & Chen Zhang, Soochow University, arXiv 2602.02307 (Feb 2026). Releases (a) a CI build dataset from 1,960 open-source Java projects and (b) a curated flaky-failure dataset from 10 projects. Full category list: https://flaky-build.github.io/
2. **Build our own** via the GitHub REST API, copying the paper's method: project selection with SEART GitHub Search; crawler saving build metadata, job metadata and one log per job; reruns detected with `run_attempt > 1`; a job is flaky if it shows both success and failure across attempts. **GitHub keeps build history ~3 months by default**, so collection must start early and run continuously.
3. **Other artifacts:** FlakeRanker (ICSE SEIP 2025) provides a labeling tool for flaky jobs and failure categories (not GitHub Actions-specific).

**Recommendation:** use the paper's data for RQ1 (fair comparison) + a small **fresh dataset collected after the LLM's training cutoff** (the paper's data dates from Sep 2023 to Aug 2024 and may have been seen by the LLM during training).

**Useful findings from the paper:**
- Its Table 1 maps each failure category → root causes → mitigation (e.g. corrupted cache → refresh cache; expired token → refresh token; rate limit → rerun). Ready-made basis for our RQ2 taxonomy and RQ3 action list.
- Labels are noisy: many failed jobs are never rerun. The authors built **CI-Rerunner**, which reruns each failed job up to 10 times on the same commit to label it.
- **Flaky tests are 64.99% of flaky failures**; the authors focused cause analysis on the other categories. **[to confirm]** whether flaky tests are in our scope.
- Other prevalent categories: network issues (15.8%), dependency resolution (6.32%), external environment inconsistency (4.7%).

### Block 2: Baseline (FlakeDetector)
- **How it works:** filters and normalizes logs, embeds them (SentenceTransformer all-MiniLM-L6-v2), stores them in a FAISS vector database built from training data only, retrieves the top-K most similar historical logs, and feeds their similarity scores and labels to a logistic regression. Also uses structured features (developer, code change, project context) from a tool called CI-Miner.
- **Why it needs training:** it is a machine learning model with no built-in knowledge. Its published F1 (0.80) was obtained by training and testing **on the same project**.
- **What we must do for RQ1:** rerun it in the **cold-start** setting: train on other projects, test on a new one (the same unseen projects as the agent). If a pretrained model is shared, testing on unseen projects is still required.
- **[to check]** availability of its code and replication package.

### Block 3: The agent
- **Inputs:** job log, commit diff, job history.
- **Tools:** e.g. `get_failed_step_log()`, `get_diff()`, `get_job_history()`.
- **Output:** strict JSON: `is_flaky`, `category` (fixed taxonomy), `action` (fixed action list), `justification` citing the log lines used as evidence.
- **Main technical challenge:** logs are huge. A **log reducer** must keep only relevant parts (failed step, error lines, last N lines) to control cost and quality.

Agent architecture (system view):
```
GitHub Actions (failed job)
        │
        ▼
 ┌─────────────────────────────────────┐
 │               Agent                 │
 │   LLM  ◄──►  Tools:                 │
 │              • get_failed_step_log  │
 │              • get_diff             │
 │              • get_job_history      │
 │   Log reducer (keeps relevant lines)│
 └───────────────┬─────────────────────┘
                 ▼
   JSON verdict: is_flaky, cause, action, justification
```

### Block 4: Evaluation

| RQ | Measure |
|---|---|
| RQ1 | Precision / recall / F1 for flaky detection: agent vs FlakeDetector, on unseen projects |
| RQ2 | Accuracy of the cause category vs manual labels + confusion matrix |
| RQ3 | Agreement between recommended action and action actually taken |
| RQ4 | LLM cost per diagnosis vs cost of avoided reruns (CI minutes + developer time) |

Plus: rate of real failures labeled flaky (dangerous errors) and **consistency** across repeated runs.

**RQ4 explained.** Cost side: LLM tokens per diagnosis (+ latency). Savings side: avoided useless reruns (CI minutes + developer waiting/attention time), in two cases: real failures (agent says "fix the code") and flaky failures where rerun won't help (corrupted cache, expired token). Complications: agent errors cause reruns too; costs vary by model and log size (compare configurations); developer time is hard to price → answer with an explicit **cost model** with documented assumptions, filled with real token counts and rerun counts from the dataset. The paper's cost metrics (waiting time, computational time per rerun sequence) can be reused.

### Risks identified
1. **Data contamination:** the LLM may have seen public logs → use failures after the model's training cutoff.
2. **Noisy labels:** "passed on rerun" is imperfect; many failed jobs are never rerun.
3. **Comparability:** FlakeDetector was evaluated on Java projects → stay on similar projects at least for RQ1.

### Proposed team split
- **Person A: data** (mining GitHub, labeling, taxonomy)
- **Person B: agent** (log reduction, tools, prompts, output schema)
- **Person C: baseline + evaluation** (FlakeDetector, metrics, cost model)
- Shared: state of the art at the start, report at the end.

---

## 7. Final product

In a research project, the **agent + evaluation harness** are the main deliverables; the user-facing product is secondary. A standalone web app with GitHub OAuth was considered but is not the most natural option (extra place to check, OAuth/security work, little research value).

### Recommended structure: three layers
```
           ┌──────────────────────────┐
           │  Core engine (Python)    │  diagnose(job) → JSON
           └──────┬─────────────┬─────┘
                  │             │
       ┌──────────▼───┐   ┌─────▼──────────────┐
       │ Evaluation   │   │ GitHub Action      │
       │ harness      │   │ (demo integration) │
       │ (batch, RQs) │   │                    │
       └──────────────┘   └────────────────────┘
```
1. **Core engine (must have):** Python package.
2. **Evaluation harness (must have):** runs the engine on the dataset, computes metrics, logs tokens and costs.
3. **GitHub Action (should have):** real-world integration and live demo.
4. **Optional:** GitHub App, or a small results dashboard for the soutenance.

### GitHub Action design
- **Trigger:** a separate workflow on `workflow_run` (types: completed, conclusion == failure). Existing CI files stay untouched; logs are complete. (Alternative: a last job with `needs` + `if: failure()`.)
- **Permissions:** `actions: read`, `contents: read`, `pull-requests: write`.
- **Evidence via GitHub REST API** (built-in `GITHUB_TOKEN`):
  - failed jobs: `GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs`
  - logs: `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs`
  - diff: `GET /repos/{owner}/{repo}/commits/{sha}` or the PR's files
  - history: `GET /repos/{owner}/{repo}/actions/workflows/{id}/runs`
- **Packaging:** a composite action (`action.yml` + Python engine) in a public repo, used with `uses: your-org/flaky-agent@v1`; LLM key stored as a repo secret.
- **Output:** job summary (`$GITHUB_STEP_SUMMARY`), PR comment, and/or annotation, e.g. *"Flaky · Cause: network · Action: rerun · Justification: Maven failed to download junit-5.10.jar with Connection reset (line 1204); no dependency or test files changed; job passed on the previous 9 runs."*

### Precautions
- `workflow_run` only works if the agent workflow is on the **default branch**.
- **Recommend, don't act:** the agent suggests a rerun, it doesn't trigger one (auto-rerun only as an opt-in feature needing `actions: write`).
- **Prompt injection:** logs and diffs can contain attacker-controlled text (fork PRs) → read and comment only, minimal permissions, never run PR code in this workflow.
- **Cost control:** diagnose failed jobs only, reduce logs, log token counts (feeds RQ4).
- **Demo:** a test repo with intentionally flaky jobs (random network failure, corrupted cache, etc.) to show each category live.

---

## 8. Research process (order of work)

1. **State of the art:** Agentic DevOps, flaky tests/builds literature, FlakeDetector and its baselines, LLM agents for software engineering, LLM non-determinism.
2. **Problem formulation:** refine RQs, taxonomy, action list, scope decisions.
3. **Design:** agent architecture, log reduction, output schema.
4. **Implementation:** engine, harness, baseline, GitHub Action.
5. **Evaluation:** experiments and results for RQ1–RQ4.
6. **Discussion:** limits, threats to validity, open questions.

---

## 9. Open questions for the supervisor

- [ ] Is the PFE-level scope adjusted to the projet d'expertise timeline?
- [ ] Are FlakeDetector's code and datasets available (replication package)?
- [ ] Are flaky tests (including concurrency-related ones) in scope?
- [ ] For real failures: minimal verdict or level-2 diagnosis (failure type + likely culprit)?
- [ ] Stay on Java projects for comparability, or include other languages?
- [ ] Which LLM: commercial API or local open model? Budget available?
- [ ] Expected final output: usable tool, evaluation results, or both? Report format and language?
- [ ] Key dates: milestones, final deadline, soutenance; meeting frequency.

---

## 10. References

- Ge, W., & Zhang, C. (2026). *Understanding and Detecting Flaky Builds in GitHub Actions*. arXiv:2602.02307. https://arxiv.org/abs/2602.02307v1
- Flaky build project website (full category list): https://flaky-build.github.io/
- *On the Diagnosis of Flaky Job Failures: Understanding and Prioritizing Failure Categories* (ICSE SEIP 2025) and its FlakeRanker artifact: https://arxiv.org/html/2503.12312v1
- flakehunter (example LLM-based flaky CI tool): https://pkg.go.dev/github.com/mdryaaan/flakehunter@v0.1.0
