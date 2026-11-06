"""In-memory workflow orchestration, independent of the HTTP interface."""
import math
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from runtime import WorkflowContext
from workflows import WORKFLOWS

from starter.agents import AgentExecutor, AgentRequest, MockAgent


class Stopped(Exception):
    pass


class Engine:
    def __init__(self, executor: AgentExecutor | None = None, workers=None, workflows=None):
        if workers is not None and not 1 <= workers <= 64:
            raise ValueError('Worker limit must be between 1 and 64')
        self.executor = executor or MockAgent()
        self.workers = workers
        self.slots = threading.BoundedSemaphore(workers) if workers is not None else nullcontext()
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.runs = {}
        self.workflows = dict(WORKFLOWS if workflows is None else workflows)
        self.active = 0

    def launch(self, workflow, inputs, run_budget=None, call_budget=None, *, name=None, demo=False):
        if workflow not in self.workflows:
            raise ValueError('Unknown workflow')
        if not isinstance(inputs, dict):
            raise ValueError('Inputs must be an object')
        for budget in (run_budget, call_budget):
            if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget <= 0):
                raise ValueError('Budgets must be positive numbers')
        definition = self.workflows[workflow]
        if hasattr(definition, 'validate'):
            definition.validate(inputs)
        run = {'id': uuid.uuid4().hex[:12], 'workflow': workflow, 'inputs': inputs,
               'status': 'running', 'created': time.time(), 'steps': [], 'output': None,
               'error': None, 'cost_usd': 0, 'tokens_in': 0, 'tokens_out': 0,
               'run_budget': run_budget, 'call_budget': call_budget, 'stop_reason': None,
               'name': name, 'demo': demo, 'events': [], 'sessions': {}}
        with self.lock:
            self.runs[run['id']] = run
            self._event(run, 'run_started', f'Workflow {workflow} started')
        threading.Thread(target=self._run, args=(run,), daemon=True).start()
        return run['id']

    def _event(self, run, kind, message, step=None, data=None):
        # Callers hold self.lock; appending under the same lock preserves event order.
        event = {'seq': len(run['events']) + 1, 'time': time.time(),
                 'kind': kind, 'message': message}
        if step is not None:
            event.update(step_id=step['id'], session_id=step['session_id'], role=step['role'], iteration=step['iteration'])
        if data is not None:
            event['data'] = data
        run['events'].append(event)

    def snapshot(self):
        import copy
        with self.lock:
            return copy.deepcopy({'runs': sorted(self.runs.values(), key=lambda r: r['created'], reverse=True), 'workers': self.workers, 'active_workers': self.active})

    def summaries(self):
        with self.lock:
            runs = []
            for run in sorted(self.runs.values(), key=lambda r:r['created'], reverse=True):
                summary = {key:run[key] for key in ('id','name','workflow','status','created','cost_usd','error')}
                summary['steps'] = [{key:step.get(key) for key in ('id','role','status','chat_status','question','chat_question','session_id')}
                                    for step in run['steps'] if step['status']=='waiting' or step.get('chat_status')=='waiting']
                runs.append(summary)
            return {'runs':runs, 'workers':self.workers, 'active_workers':self.active}

    def run_snapshot(self, run_id):
        import copy
        with self.lock:
            if run_id not in self.runs:
                raise KeyError('Run not found')
            return copy.deepcopy(self.runs[run_id])

    def answer(self, run_id, step_id, answer):
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Answer cannot be empty')
        with self.changed:
            run = self.runs.get(run_id)
            if not run:
                raise KeyError('Run not found')
            step = next ( (s for s in run['steps'] if s['id'] == step_id), None)
            if not step or step['status'] != 'waiting' or run['stop_reason']:
                raise ValueError('Question is no longer awaiting an answer')
            self._event(run, 'answer_received', 'Answer received; step queued to resume', step, {'answer': answer.strip()})
            step['answers'][step['question']['key']] = answer.strip()
            step['question'] = None
            step['status'] = 'queued'
            self.changed.notify_all()

    def message(self, run_id, step_id, text):
        """Send a follow-up without changing the completed workflow step output."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Message cannot be empty')
        with self.changed:
            run = self.runs.get(run_id)
            if not run:
                raise KeyError('Run not found')
            step = next((s for s in run['steps'] if s['id'] == step_id), None)
            if not step:
                raise KeyError('Step not found')
            if step['status'] == 'waiting':
                self.answer(run_id, step_id, text)
                return
            if step.get('kind') == 'compute':
                raise ValueError('This is a deterministic computation, not a chat agent')
            if step['status'] != 'completed':
                raise ValueError('The agent is still processing; wait for this call to finish')
            if run['stop_reason']:
                raise ValueError('This run has stopped; no further calls can be made')
            if step.get('chat_status') in ('queued', 'running'):
                raise ValueError('Wait for the previous message to finish')
            if step.get('chat_question'):
                step.setdefault('chat_answers', {})[step['chat_question']['key']] = text.strip()
                step['chat_question'] = None
            step.setdefault('chat_history', []).append({'role': 'user', 'content': text.strip()})
            step['chat_status'] = 'queued'
            self._event(run, 'chat_message', 'Message sent', step, {'answer': text.strip()})
        threading.Thread(target=self._chat_call, args=(run, step), daemon=True).start()

    def _chat_call(self, run, step):
        with self.slots:
            with self.changed:
                if run['stop_reason']:
                    step['chat_status'] = 'stopped'
                    self._event(run, 'chat_failed', run['stop_reason'], step)
                    return
                step['chat_status'] = 'running'
                self.active += 1
                self._event(run, 'chat_started', 'Processing your message', step)
                remaining = None if run['run_budget'] is None else max(0, run['run_budget'] - run['cost_usd'])
                request = AgentRequest(step['role'], 'Respond to the user follow-up about this completed step.',
                    {'original_inputs': step['inputs'], 'original_output': step['output'], 'messages': list(step['chat_history'])},
                    {'iteration': step['iteration'], 'answers': dict(step['answers']) | dict(step.get('chat_answers', {})), 'remaining_budget_usd': remaining})
            try:
                result = self.executor.run(request)
            except Exception as exc:
                with self.changed:
                    step['chat_status'] = 'failed'
                    self._event(run, 'chat_failed', str(exc), step)
            else:
                with self.changed:
                    usage = asdict(result.usage)
                    usage['cost_usd'] = result.usage.cost_usd
                    run['tokens_in'] += result.usage.tokens_in
                    run['tokens_out'] += result.usage.tokens_out
                    run['cost_usd'] = round(run['cost_usd'] + result.usage.cost_usd, 6)
                    if run['call_budget'] is not None and result.usage.cost_usd > run['call_budget']:
                        run['stop_reason'] = f'Call budget exceeded by {step["role"]}'
                    if run['run_budget'] is not None and run['cost_usd'] >= run['run_budget']:
                        run['stop_reason'] = 'Run budget reached or exceeded'
                    if result.question:
                        step['chat_history'].append({'role': 'assistant', 'content': result.question.text})
                        step['chat_question'] = asdict(result.question)
                        step['chat_status'] = 'waiting'
                        self._event(run, 'chat_question', result.question.text, step, {'question': step['chat_question'], 'usage': usage})
                    else:
                        step['chat_history'].append({'role': 'assistant', 'content': result.output})
                        step['chat_status'] = 'idle'
                        self._event(run, 'chat_completed', 'Agent replied', step, {'output': result.output, 'usage': usage})
                    if run['stop_reason']:
                        step['chat_status'] = 'stopped'
                        self._event(run, 'chat_failed', run['stop_reason'], step)
                        self.changed.notify_all()
            finally:
                with self.lock:
                    self.active -= 1

    def _call(self, run, role, inputs, iteration=0, answers=None, dependencies=None, display_name=None, item_index=None, session_key=None, instructions=None):
        step = {'id': uuid.uuid4().hex[:10], 'role': role, 'iteration': iteration,
                'status': 'queued', 'inputs': inputs, 'output': None, 'question': None,
                'answers': dict(answers or {}), 'attempts': [], 'error': None,
                'dependencies': list(dependencies or []), 'display_name': display_name, 'item_index': item_index,
                'kind': 'agent', 'instructions': instructions or f'{role} workflow step'}
        with self.lock:
            self._register_step(run, step, session_key)
            self._event(run, 'queued', 'Waiting for an available worker', step)
        while True:
            with self.slots:
                with self.changed:
                    if run['stop_reason']:
                        step['status'] = 'stopped'
                        raise Stopped()
                    step['status'] = 'running'
                    attempt_id = uuid.uuid4().hex
                    self._event(run, 'call_started', f"Calling {role} (attempt {len(step['attempts']) + 1})", step, {'attempt_id':attempt_id})
                    self.active += 1
                    remaining = None if run['run_budget'] is None else max(0, run['run_budget'] - run['cost_usd'])
                    request = AgentRequest(role, step['instructions'], inputs, {'iteration': iteration, 'answers': dict(step['answers']), 'remaining_budget_usd': remaining})
                try:
                    result = self.executor.run(request)
                except Exception as exc:
                    with self.changed:
                        step['status'] = 'failed'
                        step['error'] = str(exc)
                        run['stop_reason'] = f'{role} failed: {exc}'
                        run['stop_type'] = 'failure'
                        self._event(run, 'call_failed', str(exc), step)
                        self.changed.notify_all()
                    raise
                finally:
                    with self.lock:
                        self.active -= 1
                with self.changed:
                    usage = asdict(result.usage)
                    usage['cost_usd'] = result.usage.cost_usd
                    usage['attempt_id'] = attempt_id
                    step['attempts'].append(usage)
                    run['tokens_in'] += result.usage.tokens_in
                    run['tokens_out'] += result.usage.tokens_out
                    run['cost_usd'] = round(run['cost_usd'] + result.usage.cost_usd, 6)
                    if run['call_budget'] is not None and result.usage.cost_usd > run['call_budget']:
                        run['stop_reason'] = f'Call budget exceeded by {role}'
                    if run['run_budget'] is not None and run['cost_usd'] >= run['run_budget']:
                        run['stop_reason'] = 'Run budget reached or exceeded'
                    if result.question:
                        step['question'] = asdict(result.question)
                        step['status'] = 'waiting'
                        self._event(run, 'question', result.question.text, step, {'question': asdict(result.question), 'usage': usage})
                    else:
                        step['output'] = result.output
                        step['status'] = 'completed'
                        self._event(run, 'call_completed', f'{role} returned a result', step, {'output': result.output, 'usage': usage})
                    if run['stop_reason']:
                        if result.question:
                            step['status'] = 'stopped'
                        self.changed.notify_all()
                        raise Stopped()
                    if not result.question:
                        return result.output, step['id']
            with self.changed:
                while step['status'] == 'waiting' and not run['stop_reason']:
                    self.changed.wait()
                if run['stop_reason']:
                    step['status'] = 'stopped'
                    raise Stopped()

    def _register_step(self, run, step, session_key=None):
        if run['stop_reason']:
            raise Stopped()
        by_id = {s['id']: s for s in run['steps']}
        if any(dep not in by_id or by_id[dep]['status'] != 'completed' for dep in step['dependencies']):
            raise ValueError('Dependencies must complete before a step can execute')
        key = session_key or step['id']
        session = run['sessions'].setdefault(key, {'id':uuid.uuid4().hex[:12],
            'key':key, 'role':step['role'], 'invocation_ids':[]})
        step['session_id'] = session['id']
        session['invocation_ids'].append(step['id'])
        run['steps'].append(step)

    def _compute(self, run, name, inputs, function, iteration, dependencies):
        step = {'id':uuid.uuid4().hex[:10], 'role':'compute', 'display_name':name,
                'kind':'compute', 'iteration':iteration, 'inputs':inputs, 'output':None,
                'question':None, 'answers':{}, 'attempts':[], 'error':None,
                'status':'running', 'dependencies':list(dependencies), 'item_index':None}
        with self.lock:
            self._register_step(run, step)
            self._event(run, 'compute_started', name, step)
        try:
            output = function(inputs)
        except Exception as exc:
            with self.changed:
                step['status'] = 'failed'
                step['error'] = str(exc)
                run['stop_reason'] = str(exc)
                self._event(run, 'call_failed', str(exc), step)
                self.changed.notify_all()
            raise
        with self.lock:
            step['output'] = output
            step['status'] = 'completed'
            self._event(run, 'compute_completed', name, step, {'output':output})
        return output, step['id']

    def _run(self, run):
        try:
            definition = self.workflows[run['workflow']]
            execute = definition.run if hasattr(definition, 'run') else definition
            outcome = execute(WorkflowContext(self, run), run['inputs'])
            with self.lock:
                if run['stop_reason']:
                    raise Stopped()
                run['output'] = outcome.output
                run['status'] = outcome.status
                run['error'] = outcome.reason
                self._event(run, 'run_completed' if outcome.status == 'completed' else 'not_converged',
                            outcome.reason or 'Workflow completed', data={'output':outcome.output})
        except Stopped:
            with self.lock:
                run['status'] = 'failed' if run.get('stop_type') == 'failure' else 'stopped'
                run['error'] = run['stop_reason']
                self._event(run, 'run_stopped', run['error'])
        except Exception as exc:
            with self.changed:
                run['stop_reason'] = run['stop_reason'] or str(exc)
                run['status'] = 'failed'
                run['error'] = run['stop_reason']
                self._event(run, 'run_failed', run['error'])
                self.changed.notify_all()
