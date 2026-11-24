# Labeling guide

## 1. Definitions

A job is **flaky** if, across attempts of the same run, on the same commit, it has at least
one failure and at least one success. A failure is a `failure` or `timed_out` conclusion.

A job is **not_flaky** if it has at least 2 attempts of the same run, on the same commit, and
all of them failed.

A job is **unknown** if we don't have enough attempts to judge. An unknown job is never
counted as not_flaky: one more attempt could have succeeded and made it flaky.

Cancelled and skipped attempts are ignored: they ended without an outcome, so they say nothing
about flakiness.

The item to label is one **job** of one run, with all its attempts. Its category and root cause
are read from the log of its **first failed attempt**: that is the only log the agent would have
when the failure happens, before any rerun.

A job that passed on rerun only because another repo changed in between is still flaky. Its root
cause is Upstream Repository Issue, and its action is not Rerun.

## 2. The 18 categories

### Network Issue

**Definition:** the CI runner couldn't reach a remote host or lost its connection to it for a
transient reason, and that network operation is itself what failed, not part of a named stage
such as resolving dependencies, compiling, static analysis or tests.

**Log signs (provisional):** `java.net.SocketTimeoutException`,
`java.net.ConnectException: Connection refused`, `Connection reset`,
`java.net.UnknownHostException`, `TLS handshake timeout`, `curl: (28)`, `502 Bad Gateway`.

**Warning:** `Connection refused` on `localhost` isn't a network problem: the local server
wasn't ready yet, which is a timing problem.

### Dependency Resolution Issue

**Definition:** the job failed while retrieving a required dependency from a remote repository,
because the repository couldn't be reached or the dependency wasn't there.

**Root causes:**

- **Network Issue:** the download failed: the server didn't give a usable answer (timeout,
  reset, HTTP 5xx). Action: Rerun.
- **Missing Dependency:** the server answered, and the dependency wasn't available yet. Action:
  Upload Artifact.

**Log signs (provisional):** `Could not transfer artifact` with a connection error (Network
Issue); `Could not find artifact`, `'dependencies.dependency.version' ... is missing` (Missing
Dependency).

### External Environment Inconsistency

**Definition:** the job failed because something outside the repo's code, not under version
control, was in the wrong state or behaved inconsistently, even though the job could reach it.

**Root causes:**

- **Workflow Policy Violation:** the PR doesn't meet a policy (missing label, not approved, still
  a draft). Action: Add Label, Approve Pull Request or Mark Pull Request as Ready.
- **Authentication Failure:** expired token, or user not on the allowlist. Action: Refresh Token
  or Add to Allowlist.
- **Artifact Conflict:** stale or incompatible artifacts from a previous run. Action: Delete
  Artifact.
- **Tool Intermittent Failure:** an external tool misbehaves with the same inputs. Action: Rerun.
- **Upstream Repository Issue:** another repo or image the job relies on is out of step. Action:
  Fix Upstream Issue.

To choose between the last two, ask: with the same inputs, would the tool fail again? If it
would, the root cause is Upstream Repository Issue. If not, it's Tool Intermittent Failure.

**Example:** in ehrbase job 97370273763, the `ehrbase/integration-tests:latest` image didn't yet
contain the path the PR expected: Upstream Repository Issue.

### API Service Unavailable

**Definition:** the job exceeded the usage quota of an external service, which refused further
requests.

**Root cause:** API Rate Limit. Action: Rerun, after the quota recovers, or switch token.

### Concurrency Issue

**Definition:** tasks or threads of the job interfered with each other, because a shared
resource was accessed without proper synchronization. A failure inside a test is Flaky Test.

**Root causes:**

- **Lock Contention:** tasks compete for the same lock. Action: Rerun.
- **Concurrent Collection Modification:** a collection is changed while another thread reads it.
  Action: Rerun.

### Compilation Error

**Definition:** the job failed during the compilation stage, on code the same commit also
compiled successfully.

**Root causes:**

- **Network Issue:** Action: Rerun.
- **Corrupted Cache** or **Dirty Cache:** invalid or leftover cached files are restored. Action:
  Refresh Cache.
- **Upstream Repository Issue:** Action: Fix Upstream Issue.
- **Runner Incompatibility:** the runner lacks a required feature. Action: Update Runner.
- **Tool Intermittent Failure:** Action: Rerun.
- **Missing Dependency:** Action: Upload Artifact.

### Execution Crash

**Definition:** a process or the execution environment failed to start, or stopped abruptly
without a clean exit.

**Root causes:**

- **Out Of Memory:** the process was killed with no language-level error (`Killed`, exit 137).
  Action: Rerun.
- **Unstable Runner Environment:** the runner itself crashed, for example during initialization.
  Action: Rerun.

### File System Interaction Error

**Definition:** the job failed on an operation on the local file system, such as reading a file
that an earlier step should have provided.

**Root causes:**

- **Network Issue:** an earlier download failed without stopping the job. Action: Rerun.
- **API Rate Limit:** the same, because the download was throttled. Action: Rerun.
- **External Resource Inconsistency:** an external resource was unavailable or inconsistent.
  Action: Update External Resource.
- **Unstable Cache Key:** the cache state differs between attempts. Action: Rerun.
- **Authentication Failure:** missing permissions on the resource. Action: Update Permission.

### Static Analysis Error

**Definition:** the job failed while running a static analysis tool.

**Root causes:**

- **Upstream Repository Issue:** Action: Fix Upstream Issue.
- **Network Issue:** Action: Rerun.
- **External Resource Inconsistency:** Action: Update External Resource.
- **Stale Cache:** Action: Refresh Cache.
- **API Rate Limit:** Action: Rerun.

### Memory Limit

**Definition:** the job used more memory or disk than the execution environment allows, and the
log reports it as such.

**Root causes:**

- **Heap Memory Exhaustion:** the heap exceeded its limit (`OutOfMemoryError`). Action: Rerun.
- **Disk Space Exhaustion:** the runner ran out of storage (`No space left on device`). Action:
  Clean Disk Space.

The next five categories come from https://flaky-build.github.io/, which lists cases but no
definitions: the definitions below are ours, written from those cases.

### Code Logic Errors

**Definition:** the project's own code (not a test) threw an error at run time, and the same
commit also ran without it. Most of the site's cases have Network Issue as root cause: the code
got bad or empty data from a failed request. Action: usually Rerun.

### Plugin/Tool Errors

**Definition:** a third-party tool, plugin or action used by the job (a parser, `jq`, a Maven
plugin, another repo's action...) failed, for example on an error response it didn't check for.
Action: usually Rerun.

### Git Repository Errors

**Definition:** a Git operation of the job (push, merge, checkout) failed, typically because the
remote branch changed while the job was running. Action: usually Rerun.

### Cache Errors

**Definition:** saving or restoring the CI cache failed, for example a cache file over a size
limit. Action: Rerun, or Refresh Cache.

### Build Script Errors

**Definition:** a command in the workflow or one of its scripts failed (`curl`, `tar`, a shell
line), typically because it received a failed download and didn't check it. Action: usually
Rerun.

### Flaky Test

**Definition:** a test failed, and the same test passed on another attempt of the same commit.
Excluded from Ge & Zhang's Table 1 (64.99% of their flaky failures).

**Labeled at the category level only:** no root cause. Action: Rerun and Report Test.

For reference, Luo et al. (2014, section 3.1) give these root causes of flaky tests: Async Wait,
Concurrency, Test Order Dependency (77% of their cases together), Resource Leak, Network, Time,
IO, Randomness, Floating Point, Unordered Collection.

### Genuine Failure

**Definition:** the failure comes from a defect in the commit itself, and a rerun fails the same
way. Usually labeled not_flaky. Action: Fix the Code.

### Other

**Definition:** no category above fits. Describe the failure in a note: if several jobs share
it, we may add a category.

## 3. How to label a job

Label alone: don't discuss a job with the other annotator until both of you have labeled it.

1. **Open the job** with `uv run flaky-show job <job_id>`, which prints its failed steps, first
   error lines and GitHub link. Without the crawled data, open `<job_id>.txt` from `logs.zip`
   (made by `flaky-sample --zip`): the same summary, followed by the full log.
2. **Find the failure**, starting from the end (see below).
3. **Read the first error of that test or step.** Later errors are usually its consequences.
4. **Category: the stage that failed.** The stage wins over the cause (section 4).
5. **Root cause: why it failed**, from the list under that category. A transient failure to
   reach a remote service (timeout, reset, HTTP 5xx) is Network Issue. Flaky Test has no root
   cause: leave it empty.
6. **Label what happened, not a keyword in the log.** An `OutOfMemoryError` injected by a test,
   or a refused connection to `localhost`, is not what it looks like.
7. **Action: the one that fixes the root cause**, as listed with it in section 2. Don't
   infer it from the run history: for a flaky job, developers nearly always just reran it, and the
   delay before the rerun doesn't say why. That delay, and the number of reruns before success,
   are computed automatically from the attempts, not by the annotator.
8. **Uncertain:** if the log isn't enough, give your best label, mark it uncertain and say why in
   the note. Don't guess silently.

**Start from the end.** Find what failed the build (`BUILD FAILURE`, `<<< FAILURE!`,
`Tests run: ... Failures: 1`, the last `##[error]`), then go back to the first error of that
test or step. Earlier errors are often expected exceptions logged by tests that passed: in
tinkerpop job 121958147348, `java.lang.OutOfMemoryError: injected snapshot write error` comes
from a test that injects it on purpose, and the real failure is a timing test 6,000 lines later.

## 4. Edge cases

**Which stage?** Look at what the build was doing just before the error:

| Near the error | Stage | Category |
|---|---|---|
| `Downloading`, `Could not resolve`, `Could not transfer artifact`, `not found in ... repositories` | getting dependencies | Dependency Resolution Issue |
| `COMPILATION ERROR`, `compileJava FAILED`, `error: cannot find symbol` | compiling | Compilation Error |
| `Tests run: ... Failures: 1`, `<<< FAILURE!`, `AssertionError`, a test method name | running tests | Flaky Test if flaky, Genuine Failure if not_flaky |
| `checkstyle`, `spotbugs`, `errorprone`, `pmd`, `scorecard`, `sonar` | static analysis | Static Analysis Error |
| `git push`, `git fetch`, `! [rejected]` | Git operation | Git Repository Errors |
| `restore cache`, `save cache` | CI cache | Cache Errors |
| a `curl`, `tar` or shell command of a `run:` step | workflow script | Build Script Errors |
| a failure inside another repo's action (`uses: ...`) | third-party action | Plugin/Tool Errors |

Only when no stage applies, the category is the cause: `No space left on device` or
`OutOfMemoryError` (Memory Limit), `Killed` or exit 137 with no Java error (Execution Crash),
`429` or `rate limit` (API Service Unavailable), a bare `curl: (28)` or `Connection reset`
(Network Issue).

**The stage wins over the cause.** Four categories name a cause rather than a stage: Network
Issue, API Service Unavailable, Concurrency Issue and Memory Limit. Our rule, based on Table 1
of Ge & Zhang (2026):

- If the failure is part of a named stage (resolving dependencies, compiling, static analysis,
  tests...), the category is that stage and the cause is the root cause.
- Only if no stage applies is the category the cause itself.

Examples:

- `curl: (28) Operation timed out` in a `Download protoc` step: Network Issue → Request Timeout.
- An HTTP 502 while fetching a dependency: Dependency Resolution Issue → Network Issue.
- Maven's `Could not acquire shared lock for artifacts` in a parallel build: Dependency
  Resolution Issue → Lock Contention.
- `##[error]No space left on device`: no stage applies, so Memory Limit → Disk Space Exhaustion.

**A third-party action that stops because a token, secret or permission is missing** worked as
designed: it's External Environment Inconsistency → Authentication Failure, not Plugin/Tool
Errors. Plugin/Tool Errors is for a tool that misbehaves.

Check the host first: a refused connection to `localhost` in a test is usually a server that
wasn't ready yet, so it's a timing problem, not a network one.

## 5. Output format

Each annotator fills their own file, `labels/<annotator>.csv`, with one line per job. Don't open
and save it in Excel as is: Excel turns job ids into numbers like `1.29492E+11` and loses their
digits. Use a text editor or LibreOffice, or Excel's Data → From Text/CSV with `job_id` as Text.
Don't leave spaces around values: `no ` is not `no`.

| Column | Content |
|---|---|
| `job_id` | the job id given by `flaky-show` |
| `repo` | `owner/repo` |
| `category` | one of the 18 names of section 2, spelled exactly |
| `root_cause` | a root cause listed under that category, spelled exactly; empty for Flaky Test |
| `action` | the action listed with that root cause in section 2 |
| `uncertain` | `yes` or `no` |
| `note` | why you hesitated, or what didn't fit; empty otherwise |

Example:

```csv
job_id,repo,category,root_cause,action,uncertain,note
116754607629,datastax/jvector,Memory Limit,Disk Space Exhaustion,Clean Disk Space,no,
```
