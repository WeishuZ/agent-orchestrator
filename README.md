# Agent Orchestrator

A single-user, in-memory proof of concept. Python 3.10+, standard library only. No API keys or dependencies.

```sh
python3 app.py --port 8768
```

Open http://127.0.0.1:8768. **New run** launches a workflow; **examples** starts five sample runs. Mock calls complete immediately, with no default worker cap. Spending limits remain optional. All data is cleared on restart.

```sh
python3 -m unittest discover -s tests -v
```

## Workflows

- **Label and aggregate:** one document per line. Label calls run independently; the aggregate depends on all labels.
- **Generate and QC:** source material produces a batch of candidates. Reviewers run independently, then the gate accepts or requests revision. The generator pauses for a focus answer on its first revision. QC feedback is recorded in the generator's stable session. The API also accepts `reviewers`, a list of 1–8 reviewer role names; the default is `qc_reviewer`.
- **Distribution shaping:** one item per line; choose uniform or normal. Difficulty scores are integers 1–5. Uniform uses equal weights; normal uses weights `[1,4,6,4,1]`. Largest-remainder rounding creates an achievable integer histogram. Sorted items are assigned sorted target scores to minimize movement. Each round scores, compares, adjusts selected items, re-scores, and compares again. The graph progresses to the right with stable item rows. The API accepts a `scorer` role, defaulting to `difficulty_scorer`; a custom executor can implement it.

Distribution comparison is a deterministic compute node, so it incurs no agent usage. Reaching the five-revision limit without matching returns **not_converged**, with the partial output and reason. QC similarly stops as not converged after ten rounds without acceptance. Invalid scorer output fails explicitly.

The provided mock hardener/softener changes only numeric scores. Real executors can return revised content in `item`; it is passed into subsequent scoring. Scoring currently uses a discrete 1–5 contract, and normal distribution parameters are fixed for this example.

## Architecture

- `engine.py`: run and invocation lifecycle, stable sessions, attempts, execution, budget accounting, questions, event records, snapshots and follow-up chat.
- `runtime.py`: workflow authoring primitives: `call`, `parallel`, `compute`, `feedback`, and typed workflow outcomes/definitions.
- `workflows.py`: the three workflow definitions and their input validation. Workflows combine parallel calls, conditionals and Python loops. They are registered in `WORKFLOWS`; adding a workflow does not require editing the engine.
- `starter/agents.py`: the supplied `AgentExecutor` interface and unmodified deterministic `MockAgent`.
- `app.py` and `static/`: localhost HTTP API and browser UI.

Dependencies are checked before execution. Workflow code explicitly schedules its next phase using returned invocation IDs and outputs; this is a programmatic workflow runtime, not a declarative DAG scheduler. A session has a stable ID, contains multiple invocation IDs, and each charged attempt has its own ID. Both agent and compute nodes emit ordered events. A real executor can be injected into `Engine(executor=...)`.

Questions suspend only the relevant invocation and release its worker slot. Independent invocations and other runs continue. Answers re-issue the original invocation with its answer context. The global question inbox links directly to the waiting session. The run list supports active, needs-input, and failure filters.

## Budgets and chat

Each attempt is charged, including calls returning a question. Actual usage is checked after return. Exceeding a call limit, or reaching the run limit, prevents new calls; already-started calls finish and are charged. Final usage can therefore exceed a limit. Compute nodes are free. The engine retains an optional programmatic worker cap for real providers and testing; the local app deliberately runs the instant mock without a cap.

Click a node for inputs, results, activity, questions and session history. QC sessions group revisions by backend session identity, even if multiple reviewers use the same role. A follow-up to a completed agent makes a separate charged call containing its original input/output and chat messages. It does not replace workflow output or re-run downstream nodes. Compute nodes do not accept chat.

The mock does not stream tokens, expose thinking, or report tool calls. Follow-up responses remain deterministic role outputs, rather than natural-language conversation.

## API

- `POST /api/runs`: `workflow`, `inputs`, optional `run_budget`, `call_budget`.
- `GET /api/state?summary=1`: lightweight run summaries and pending questions.
- `GET /api/runs/{id}`: one run's full graph and event history.
- `GET /api/state`: full snapshot, useful for scripts and debugging.
- `POST /api/answer`: `run_id`, `step_id`, `answer`.
- `POST /api/message`: `run_id`, `step_id`, `message`.
- `POST /api/examples`: launch the five example runs.

## Scope

No persistence, authentication, distributed execution, automatic retries, timeouts or process cancellation. These are future extensions; current state and events are held in memory. The UI polls summaries and the selected run rather than streaming events. A future implementation could move the event sink and state storage behind interfaces and have workers consume ready invocations with leases and idempotency keys.

