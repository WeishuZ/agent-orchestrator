"""Agent execution substrate for the orchestrator exercise.

This module is the only part of the system that "runs an agent". Build your
orchestrator against the ``AgentExecutor`` interface; at grade time we run
against the provided ``MockAgent`` so every workflow is reproducible with no
API key and no cost.

Wiring a real provider (an ``LLMAgent`` implementing ``AgentExecutor``) is an
optional extension, not a requirement.

Standard library only. No wall clock, no randomness: identical requests
produce identical results.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Usage:
    """What one agent call consumed. Every call costs something."""

    tokens_in: int
    tokens_out: int

    @property
    def cost_usd(self) -> float:
        # Illustrative pricing; the point is that cost is real and additive,
        # so budget accounting has something to enforce.
        return round(self.tokens_in * 1e-6 + self.tokens_out * 4e-6, 6)


@dataclass(frozen=True)
class Question:
    """A request for human input.

    Surface ``text`` to the user, then re-issue the same request with the
    reply at ``context["answers"][key]``.
    """

    key: str
    text: str


@dataclass(frozen=True)
class AgentRequest:
    role: str
    """e.g. "labeller", "aggregator", "generator", "qc_reviewer", "gate"."""

    instructions: str
    """The step's system prompt / task."""

    inputs: dict[str, Any] = field(default_factory=dict)
    """Messages from upstream steps, keyed however you choose to wire them."""

    context: dict[str, Any] = field(default_factory=dict)
    """The system state an agent is allowed to see: loop iteration, remaining
    budget, answers to earlier questions. The MockAgent reads
    ``context["iteration"]`` and ``context["answers"]`` (see below)."""


@dataclass(frozen=True)
class AgentResult:
    output: dict[str, Any]
    """The message this step emits downstream."""

    usage: Usage

    question: Question | None = None
    """If set, the agent needs human input before it can produce output;
    ``output`` is empty. Re-run the same request with the answer placed at
    ``context["answers"][question.key]``. The attempt that asked still costs."""


class AgentExecutor(Protocol):
    """The single seam between your orchestrator and 'running an agent'."""

    def run(self, request: AgentRequest) -> AgentResult: ...


def _tokens(obj: Any) -> int:
    """A crude but deterministic token estimate."""
    return max(1, len(json.dumps(obj, sort_keys=True, default=str)) // 4)


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _walk_values(x: Any, key: str) -> list[Any]:
    """All values stored under ``key`` anywhere in a nested structure, so the
    mock does not depend on how you name your wires."""
    found: list[Any] = []
    if isinstance(x, dict):
        if key in x:
            found.append(x[key])
        for v in x.values():
            found.extend(_walk_values(v, key))
    elif isinstance(x, list):
        for v in x:
            found.extend(_walk_values(v, key))
    return found


_LABELS = ("relevant", "borderline", "irrelevant")


class MockAgent:
    """A deterministic stand-in for an LLM agent.

    Behaviour is a pure function of the request. Role-specific behaviour is
    just enough to make the reference workflows run, converge, and exercise
    pause/resume:

      * ``labeller`` emits one label, a deterministic function of its inputs.
      * ``aggregator`` counts every ``label`` found anywhere in its inputs.
      * ``generator`` emits a batch of candidate items tagged with the
        current iteration. On revisions (``context["iteration"] >= 1``) it
        requires ``context["answers"]["focus"]``; if absent it returns a
        ``Question`` instead of output.
      * ``*_reviewer`` emits ``max(0, 2 - iteration)`` findings, so QC
        converges as the batch improves. Pass the loop iteration through
        ``context``. It also flags a finding unless a non-empty
        ``candidates`` or ``candidate`` value appears somewhere in its
        inputs, so a batch produced without answering the generator's
        question never passes QC.
      * ``gate`` returns ``{"decision": "revise"}`` while any findings remain
        anywhere in its inputs, otherwise ``{"decision": "accept"}``.
      * ``difficulty_scorer`` echoes a ``difficulty`` found in its inputs, or
        derives one in 1..5 from their digest. ``hardener``/``softener`` move
        it one step up/down, clamped to 1..5.

    Any other role gets a generic deterministic digest of its inputs. Every
    call reports ``Usage``.
    """

    def run(self, request: AgentRequest) -> AgentResult:
        iteration = int(request.context.get("iteration", 0))
        answers = request.context.get("answers") or {}
        role = request.role
        question: Question | None = None

        if role == "labeller":
            d = _digest(request.inputs)
            output: dict[str, Any] = {
                "label": _LABELS[int(d[:8], 16) % len(_LABELS)],
                "doc_digest": d[:12],
            }
        elif role == "aggregator":
            counts: dict[str, int] = {}
            for label in _walk_values(request.inputs, "label"):
                counts[str(label)] = counts.get(str(label), 0) + 1
            output = {"label_counts": dict(sorted(counts.items())), "n_labels": sum(counts.values())}
        elif role == "generator":
            if iteration >= 1 and "focus" not in answers:
                question = Question(
                    key="focus",
                    text="What should the revised candidates focus on?",
                )
                output = {}
            else:
                focus = str(answers.get("focus", "baseline"))
                output = {
                    "candidates": [f"candidate-{i} focusing on {focus} (rev {iteration})" for i in range(3)]
                }
        elif role.endswith("reviewer"):
            n = max(0, 2 - iteration)
            findings = [f"qc issue #{i + 1} (rev {iteration})" for i in range(n)]
            batch = _walk_values(request.inputs, "candidates") + _walk_values(request.inputs, "candidate")
            if not any(v for v in batch):
                findings.append("empty batch: nothing to QC")
            output = {"findings": findings}
        elif role == "gate":
            open_findings = sum(
                len(f) for f in _walk_values(request.inputs, "findings") if isinstance(f, list)
            )
            output = {
                "decision": "revise" if open_findings > 0 else "accept",
                "open_findings": open_findings,
            }
        elif role in ("difficulty_scorer", "hardener", "softener"):
            found = [v for v in _walk_values(request.inputs, "difficulty") if isinstance(v, int)]
            d = found[0] if found else int(_digest(request.inputs)[:8], 16) % 5 + 1
            if role == "hardener":
                d = min(5, d + 1)
            elif role == "softener":
                d = max(1, d - 1)
            output = {"difficulty": d}
        else:
            output = {"role": role, "digest": _digest(request.inputs)[:12]}

        tokens_in = (
            _tokens(request.instructions) + _tokens(request.inputs) + _tokens(request.context)
        )
        tokens_out = _tokens(question.text) if question is not None else _tokens(output)
        return AgentResult(output=output, usage=Usage(tokens_in, tokens_out), question=question)


if __name__ == "__main__":
    # A tiny smoke test of the substrate — not a workflow.
    agent = MockAgent()

    labels = [
        agent.run(AgentRequest("labeller", "label this document", {"doc": f"doc-{i}"})).output
        for i in range(4)
    ]
    agg = agent.run(AgentRequest("aggregator", "aggregate labels", {"labels": labels}))
    print(f"aggregate: {agg.output}")

    answers: dict[str, str] = {}
    for it in range(4):
        ctx = {"iteration": it, "answers": answers}
        gen = agent.run(AgentRequest("generator", "generate candidates", {}, ctx))
        if gen.question is not None:
            print(f"iteration {it}: generator asks: {gen.question.text!r}")
            answers[gen.question.key] = "edge cases"
            gen = agent.run(AgentRequest("generator", "generate candidates", {}, ctx | {"answers": answers}))
        qc = agent.run(AgentRequest("qc_reviewer", "qc the batch", {"batch": gen.output}, ctx))
        gate = agent.run(AgentRequest("gate", "decide", {"qc": qc.output}))
        print(f"iteration {it}: gate={gate.output['decision']:7s} "
              f"open_findings={gate.output['open_findings']}")
        if gate.output["decision"] == "accept":
            break
