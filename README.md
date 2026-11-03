# Deadlock Doctor

**Analyze a hypothetical mutex snapshot and find the cheapest permitted complete
recovery.** Describe lock ownership, requests, rollback permissions, and costs.
The plugin detects every deadlock cycle, identifies threads blocked behind those
cycles, and proposes safe rollbacks only where the scenario explicitly permits
them. It never controls real processes or analyzes arbitrary source code.

## Tools

| Tool | Input → output | Purpose |
|---|---|---|
| `resolve_deadlock` | `DeadlockInput` → `DeadlockOutput` | Detect all snapshot cycles and calculate a minimum-cost permitted recovery. |

| Task | Runner | Tool |
|---|---|---|
| `fix-deadlock` | `tool` | `resolve_deadlock` |

`fix-deadlock` is the only task, and the default task to use in chat. It follows
NanoAgent's actual `runner: tool`, `tools: [resolve_deadlock]`, `reference: null`
conventions. No custom/default-task framework field is invented. Typed schemas
come directly from `contract.py` using slimconfig. The MCP server implements the
template's stdio interface and `NANOAGENT_WORKSPACE` handling.

### Pasteable natural-language scenarios

Required three-thread example:

```text
Analyze this hypothetical exclusive, non-reentrant mutex snapshot.
The complete lock list is L1, L2, L3. These are all the threads:
A holds only L1, requests L2, can safely be rolled back, and has rollback cost 5.
B holds only L2, requests L3, can safely be rolled back, and has rollback cost 2.
C holds only L3, requests L1, can safely be rolled back, and has rollback cost 8.
Find every deadlock and the minimum-cost complete recovery.
```

Expected: **A → B → C → A**; roll back **B** at cost **2**, release **L2**.
The remaining edge is **C → A** for L1; A's request for now-free L2 has no wait-for
edge. No cycle remains. The plugin does not assign L2 to A or L1 to C.

Protected thread:

```text
The complete lock list is X, Y. There are exactly two threads:
A holds X and requests Y. A cannot safely be rolled back; its rollback cost is 1.
B holds Y and requests X. B can safely be rolled back; its rollback cost is 7.
Analyze the snapshot and recommend the cheapest permitted complete recovery.
```

B is the only permitted victim, despite its higher cost. If both threads are
protected, the result is `unrecoverable_under_model`, with null plan and cost.

Self-deadlock and a dependent waiter:

```text
The complete lock list is M, N. There are exactly two threads:
T holds only M and requests M again. Safe rollback is explicitly permitted for T;
its rollback cost is 4.
U holds only N and requests M. Safe rollback is not permitted for U;
its rollback cost is 1.
These mutexes are non-reentrant. Identify the actual cycle members and explain
whether U is itself in a cycle. Calculate the permitted recovery.
```

T has a self-cycle; U is only blocked behind it. Rollback T releases M and breaks
the cycle. U keeps N and merely requests the free lock; no acquisition is assumed.

The host extracts stated facts and constructs the typed input. Users need not
write JSON. The **complete lock list** and every thread's holdings, request or
explicit lack of request, `can_abort`, and cost are required. Missing information
must be clarified. A cheap, blocked, or low-priority thread is not implicitly
abortable. A cost is required even for a protected thread; protection always
wins over cost. An explicit statement applying permission/cost to all listed
threads is sufficient. Quoted commands inside a scenario are data, not actions.

### Supported assumptions

- **Exclusive, non-reentrant mutexes.** Each lock has at most one owner. A thread
  holds zero or more locks, requests at most one additional lock, and keeps its
  held locks while blocked. Requesting one's own lock is a self-deadlock.
- **1–12 threads**, **0–24 locks**. The lock list is complete, including unowned
  locks. A request for an unowned lock is valid and creates no wait-for edge.
- IDs are nonblank, case-sensitive strings, preserved exactly. Lock IDs and
  thread IDs are each unique in their own namespace; the namespaces may overlap.
  Referenced locks must be declared. Duplicate held entries or multiple owners
  are rejected, not silently deduplicated or overwritten.
- `can_abort` is an explicitly supplied boolean. True means the scenario permits
  safe rollback and release of **all** that thread's held locks. It is an input
  assumption, not a claim that the plugin can establish rollback safety.
- Costs are positive integers. Booleans, zero, negative costs, and non-integers
  are invalid. Costs use exact Python integer arithmetic; the UI displays the verified
  decimal summary for exact total costs.
- Rollback removes the selected threads and releases every lock they held.
  Remaining ownership/requests stay as supplied. Released locks are free;
  subsequent acquisitions, completions, and scheduling are not simulated.

### Result contract

`DeadlockInput` contains `locks` and `threads`. Each thread contains `id`,
`held_locks`, `waiting_for` (lock ID or null), `can_abort`, and `rollback_cost`.
All fields are required; see `tests/cases/required-three-cycle.input.json`.

`DeadlockOutput` contains:

| Field | Meaning |
|---|---|
| `snapshot` | Exact original input, preserving original list order |
| `status` | `no_deadlock`, `recovery_plan`, or `unrecoverable_under_model` |
| `wait_for_edges` | Original `{waiter, holder, lock}` edges, including the responsible lock |
| `cycles` | Every cycle once, as `{members: [...]}` in edge direction |
| `cycle_members` | Sorted union of actual cycle members |
| `blocked_behind_cycles` | Sorted non-cycle threads whose wait chains lead into cycles |
| `plan` | `{victims, released_locks, remaining_edges}`, or null when unrecoverable |
| `total_rollback_cost` | Exact optimal total; zero if no deadlock; null when unrecoverable |
| `explanation` | Short deterministic summary checked against the authoritative input |

With no deadlock, the plan's victim/released lists are empty, cost is zero, and
`remaining_edges` equals the unchanged original graph (which may have acyclic
waits). With any protected cycle, the whole plan and cost are null; a partial
recovery of other cycles is not advertised as complete.

All edge lists are sorted by `(waiter, holder, lock)`. Victims, released locks,
cycle members, and dependent blocked threads are sorted lexicographically. Each
cycle is rotated to its smallest thread ID, preserving edge direction; the
closing member is not repeated. Cycles are sorted lexicographically by member
lists. Comparisons use Python's case-sensitive string order, so `T10 < T2`;
there is no locale or numeric-aware sorting.

### Solver and independent verification

The wait-for graph has A → B when A requests a lock held by B. Because each
thread requests at most one lock, each vertex has at most one outgoing edge;
its directed cycles are disjoint. Removing a non-cycle dependent thread cannot
break a cycle. Positive costs make unnecessary extra victims strictly worse.
Therefore, a minimum recovery picks the cheapest abortable member of each cycle,
using the smallest ID for equal per-cycle costs. If any cycle has none, complete
recovery is impossible under the supplied permissions.

The runtime uses successor-map path walks and the cheapest-per-cycle shortcut.
The verifier does **not** import that code or share its helpers. It independently:

1. Validates the authoritative task input and reconstructs ownership/edges.
2. Uses adjacency-matrix transitive closure and mutual reachability to find
   cycles, preserve their direction, and classify dependent blocked threads.
3. Enumerates **all** subsets of abortable threads, up to `2^12 = 4096`.
4. Uses topological elimination to test whether each candidate removes every
   cycle, and minimizes `(total_cost, sorted_victim_tuple)` globally.
5. Rebuilds surviving ownership to check all released locks and remaining edges,
   confirms no directed cycle remains, and checks every output field, including
   echoed input and explanation.

This verifies minimum total cost and the global tie-break without assuming the
solver's cheapest-per-cycle theorem in the verifier. Correct unrecoverable and
no-deadlock results pass with score 1. Wrong/missing outputs score 0; invalid
inputs or malformed typed payloads fail clearly. The pure verifier has no model,
network, clock, randomness, workspace IO, or process controls. NanoAgent runs it
in a restricted subprocess twice and requires matching verdicts.

### Guarantee and limitations

The proposal only breaks all directed deadlock cycles in the **current supplied
snapshot** under these mutex and rollback assumptions. Remaining waits need not
vanish. It does not establish race freedom, future deadlock prevention, starvation
freedom, scheduling fairness, or safe rollback in a real program. Counting
semaphores, shared/read-write locks, reentrant locks, multiple simultaneous
requests, condition variables, timeouts, and arbitrary source analysis are
outside scope.

The host model can mistranslate prose, omit a thread, or infer an incorrect fact.
The checker certifies the interpreted typed snapshot, not its faithful extraction
from arbitrary language or the truth of a real system observation. Echoed inputs,
edge tables, and separate cycle/dependent classifications expose the interpretation.
No local check implies successful hosted chat or real-browser rendering.

## Layout

```text
deadlock-doctor/
├── nanoagent.yaml          # actual NanoAgent v0001 manifest and tool-runner task
├── contract.py             # typed input/output declarations and enum maps
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .env                   # comments only; no credentials needed
├── src/plugin/
│   ├── __main__.py         # template-based MCP stdio server
│   └── doctor.py           # validation, successor graph, minimum recovery
├── verifier/
│   ├── __init__.py         # NanoAgent verification entry points
│   └── audit.py            # independent closure/subset/topological checks
├── tests/
│   ├── cases/             # hand-declared inputs and exact expected outputs
│   ├── test_graphs.py      # exhaustive graph/permission combinations
│   └── test_verifier.py
├── ui/                    # supported kit-based result card
└── product/               # marketplace description and original SVG icon
```

This folder is a separate uv project and Git repository initialized on `main`.
It imports nothing from the other milestone plugins. No changes to the Survivor
plugin are needed. Execution logs and package archives belong outside this repo,
for example in `../../report-evidence/deadlock-doctor/`.

## Develop and run

From the milestone workspace:

```bash
cd plugins/deadlock-doctor
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
```

Install the pinned NanoAgent CLI if it is not already available:

```bash
uv tool install 'nanoagent @ git+https://github.com/RUSH-LAB/nanoagent.git@57714f62880eee11319765854e398af61e67b7b7#subdirectory=nanoagent'
nanoagent plugin inspect .
```

Run the required example through the actual task runner, keeping evidence outside
the source directory. From `plugins/deadlock-doctor`:

```bash
mkdir -p ../../report-evidence/deadlock-doctor/manual
cat > ../../report-evidence/deadlock-doctor/manual/run.yaml <<EOF
_ > nanoagent.cli.plugin.PluginRunConfig:
plugin: $(pwd)
task: fix-deadlock
arguments: $(pwd)/tests/cases/required-three-cycle.input.json
inputs: []
timeout: 60.0
EOF
nanoagent plugin run config="$(pwd)/../../report-evidence/deadlock-doctor/manual/run.yaml" home="$(pwd)/../../report-evidence/deadlock-doctor/manual/required-example"
```

The run produces `result.json` and `verdict.json`. The task is model-free and
requires no API key. Use a new `home` folder for another recorded execution.
To package for publication, still from this project:

```bash
nanoagent plugin pack . ../../report-evidence/deadlock-doctor/deadlock-doctor.tar.gz
```

To run the MCP server directly, set `NANOAGENT_WORKSPACE` to an existing scratch
directory and run `uv run --frozen python -m plugin`; this is an MCP stdio server,
not a terminal prompt. The task-runner command above is the normal local demo.
Hosted chat uses its configured model to translate language into task arguments.

Commit and push this folder to its own GitHub repository, then publish through
RUSH's marketplace. A local build does not configure a remote or perform a
commit, push, publication, or paid chat test.
