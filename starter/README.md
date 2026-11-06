# Starter kit

The only thing scaffolded for you is the agent substrate. The orchestrator,
the workflow definitions, and the UI are yours to build.

    agents.py   AgentExecutor interface, deterministic MockAgent, Usage, Question

## Running the substrate

    python3 agents.py

This runs a smoke test of the `MockAgent` (not a workflow): a label/aggregate
pass over four documents, then the generate-and-QC loop converging to
`accept`, with the generator asking a question on the first revision:

    aggregate: {'label_counts': {'borderline': 1, 'relevant': 3}, 'n_labels': 4}
    iteration 0: gate=revise  open_findings=2
    iteration 1: generator asks: 'What should the revised candidates focus on?'
    iteration 1: gate=revise  open_findings=1
    iteration 2: gate=accept  open_findings=0

Python 3.10+, standard library only.

## The MockAgent contract

- **Deterministic.** Output is a pure function of the `AgentRequest`.
  Identical requests give identical results — no wall clock, no randomness.
- **It costs something.** Every call returns `Usage` (`tokens_in`,
  `tokens_out`, a derived `cost_usd`). Budget accounting enforces against
  these; a call that only produced a `Question` still costs.
- **It reads `context`.** This is the seam through which an agent sees system
  state. The mock reads two keys; feed them (and whatever else an agent is
  allowed to know, such as remaining budget) from your orchestrator:
  - `context["iteration"]` — reviewers emit `max(0, 2 - iteration)` findings,
    so the generate-and-QC loop converges; the gate accepts at zero findings.
    A reviewer given no candidates at all flags an extra finding, so an empty
    batch never passes QC.
  - `context["answers"]` — on revisions (`iteration >= 1`) the `generator`
    requires `answers["focus"]`. If it is absent, the result carries a
    `Question` instead of output: surface `question.text` to the user, then
    re-run the same request with the reply at
    `context["answers"][question.key]`.

Roles the mock implements: `labeller`, `aggregator`, `generator`,
`*_reviewer`, `gate`, `difficulty_scorer`, `hardener`, `softener`. Any other
role returns a deterministic digest of its inputs. See the `MockAgent`
docstring for exact behaviour.

Build against the `AgentExecutor` interface, not against `MockAgent`
directly, so a real provider could be dropped in later. At grade time we run
your engine against the provided `MockAgent`.
