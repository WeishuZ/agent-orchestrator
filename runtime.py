"""Workflow authoring primitives. Workflows compose these without knowing engine internals."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any


@dataclass
class WorkflowOutcome:
    output: dict[str, Any]
    status: str = 'completed'
    reason: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    run: Any
    validate: Any


class WorkflowContext:
    def __init__(self, engine, run):
        self.engine = engine
        self.run = run

    def call(self, role, inputs, *, iteration=0, dependencies=(), session=None,
             answers=None, item_index=None, name=None, instructions=None):
        return self.engine._call(self.run, role, inputs, iteration, answers,
            dependencies, display_name=name, item_index=item_index,
            session_key=session, instructions=instructions)

    def parallel(self, calls):
        """Run independent call specifications; questions block only their own call."""
        if not calls:
            return []
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = [pool.submit(self.call, **call) for call in calls]
            return [future.result() for future in futures]

    def compute(self, name, inputs, function, *, iteration=0, dependencies=()):
        return self.engine._compute(self.run, name, inputs, function, iteration, dependencies)

    def answers(self, invocation_id):
        with self.engine.lock:
            step = next(s for s in self.run['steps'] if s['id'] == invocation_id)
            return dict(step['answers'])

    def feedback(self, invocation_id, findings, number):
        with self.engine.lock:
            step = next(s for s in self.run['steps'] if s['id'] == invocation_id)
            self.engine._event(self.run, 'review_feedback', f'QC feedback #{number}', step,
                               {'findings': findings, 'number': number})
