# Agent Orchestrator

We are building a product in which agent pipelines are run and managed. A user
typically has tens of runs going at once and cycles between them, supplying
domain knowledge where a pipeline asks for it and reviewing output.

An **agent invocation** is one call: instructions, inputs, and output. This
exercise is about how invocations are wired together, run concurrently,
receive input, and are observed.

Design the engine which runs these pipelines and the UI the user sits in, and
build a proof of concept.

## Workflows

The engine must express and run workflows like these:

1. **Label and aggregate.** Take n documents. Each document goes through a
   labelling agent. A final agent aggregates the labels into one dataset.

       doc_1 ──> label ──┐
       doc_2 ──> label ──┼──> aggregate
       ...               │
       doc_n ──> label ──┘

2. **Generate and Quality Control (QC).** Start from a body of source
   material. A generator produces a batch of candidate items. The candidates
   run through QC checks. A gate reads the QC results and either accepts the
   item or sends it back to the generator with the findings. On revision the
   generator may need input from the user. In this case, the run pauses until
   the question is answered.

       generate ──> qc ──> gate ──(revise; generator may ask the user)──> generate
                             │
                             └──(accept)──> done

3. **Distribution shaping.** Take an existing set of items, a scoring
   function, and a target difficulty distribution (uniform or normal). Score
   every item in parallel, compare the empirical distribution against the
   target distribution, harden or soften individual items, re-score, repeat
   until the distribution matches the target.

## Human input

Any invocation may return a question instead of an output.

- That step pauses. Independent steps of the same run, and all other runs,
  keep executing.
- The question surfaces in the UI. The user answers; the step continues.

## Deliverables

1. **A UI to orchestrate these workflows.** Launch runs, watch their state,
   and answer pending questions.
2. **The engine underneath.** What executes workflows and how they are wired
   together.
3. **Example workflows.** Can be from the set above.
4. **`WRITEUP.md`.** Anything you want to add in writing instead of code.

## Proof-of-concept simplifications

- Can be one in-memory process. No distributed execution or persistence.
- Use `MockAgent` in `starter/agents.py` to avoid using or paying for an LLM.
- Single-user interface without auth, rate limiting, etc.

The abstractions should plausibly extend to real agents, persistence, and
distribution.

## What is provided

    starter/agents.py   AgentExecutor, MockAgent, Usage, Question
    starter/README.md   how to run it, and the MockAgent contract

The `MockAgent` is deterministic and every call returns `Usage`. QC reviewers
shed findings as `context["iteration"]` rises so the generate-and-QC loop
converges, and the generator asks a question on revisions so pause/resume is
exercised. Nothing else is scaffolded.

## Time and tools

Two hours. The scope is larger than two hours of manual thinking and code
writing. That is deliberate. Use your own editor, your own environment, and
any LLM coding assistants you use day to day.

## Evaluation

Will be evaluated on ideas and how strong the abstractions are. Code quality
should be reasonable, but does not need to be great.

## Submission

Submit your repository through the portal (an uploaded archive or a pushed
repository), including `WRITEUP.md` at the top level, and run instructions for
your example workflows. Please hand write `WRITEUP.md` (interested in your
process independent of the LLM).
