import threading
import time
import unittest
from engine import Engine
from starter.agents import AgentResult, MockAgent, Usage


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('Timed out waiting for state')


def run_state(engine, rid):
    return next(r for r in engine.snapshot()['runs'] if r['id'] == rid)


class EngineTests(unittest.TestCase):
    def test_label_and_distribution(self):
        e = Engine(workers=2)
        for workflow, inputs in [('label', {'documents':['a','b','c']}), ('distribution', {'items':['a','b','c','d','e'], 'target':'uniform'}), ('distribution', {'items':list('abcdefgh'), 'target':'normal'})]:
            rid = e.launch(workflow, inputs)
            r = wait_for(lambda: (r if (r := run_state(e, rid))['status'] != 'running' else None))
            self.assertEqual(r['status'], 'completed', r['error'])
            if workflow == 'label':
                self.assertEqual(r['output']['n_labels'], 3)
            else:
                self.assertTrue(r['output']['matched'])
                self.assertEqual(sum(r['output']['target_counts']), len(inputs['items']))

    def test_question_releases_worker_and_resumes(self):
        e = Engine(workers=1)
        rid = e.launch('qc', {'source':'source'})
        r = wait_for(lambda: next((x for x in [run_state(e, rid)] if any(s['status']=='waiting' for s in x['steps'])), None))
        step = next(s for s in r['steps'] if s['status']=='waiting')
        self.assertEqual(e.snapshot()['active_workers'], 0)
        other = e.launch('label', {'documents':['a','b']})
        wait_for(lambda: run_state(e, other)['status'] == 'completed')
        e.answer(rid, step['id'], 'edge cases')
        r = wait_for(lambda: (r if (r := run_state(e,rid))['status'] != 'running' else None))
        self.assertEqual(r['status'], 'completed')
        self.assertGreaterEqual(sum(len(s['attempts']) for s in r['steps']), 10)

    def test_budget_and_question_charge(self):
        e = Engine(workers=1)
        rid = e.launch('qc', {'source':'source'}, call_budget=.000001)
        r = wait_for(lambda: (r if (r := run_state(e,rid))['status'] != 'running' else None))
        self.assertEqual(r['status'], 'stopped')
        self.assertEqual(len(r['steps']), 1)
        self.assertGreater(r['cost_usd'], .000001)
        rid = e.launch('label', {'documents':['a','b']}, run_budget=.000001)
        r = wait_for(lambda: (r if (r := run_state(e,rid))['status'] != 'running' else None))
        self.assertEqual(r['status'], 'stopped')
        self.assertGreater(r['cost_usd'], 0)

    def test_graph_dependencies_capture_parallel_join_and_qc_loop(self):
        e = Engine(workers=2)
        rid = e.launch('label', {'documents': ['a', 'b', 'c']})
        r = wait_for(lambda: (x if (x := run_state(e, rid))['status'] == 'completed' else None))
        labels = [s for s in r['steps'] if s['role'] == 'labeller']
        agg = next(s for s in r['steps'] if s['role'] == 'aggregator')
        self.assertTrue(all(not s['dependencies'] for s in labels))
        self.assertEqual(set(agg['dependencies']), {s['id'] for s in labels})
        rid = e.launch('qc', {'source': 'source'})
        r = wait_for(lambda: next((x for x in [run_state(e, rid)] if any(s['status'] == 'waiting' for s in x['steps'])), None))
        step = next(s for s in r['steps'] if s['status'] == 'waiting')
        first_gate = next(s for s in r['steps'] if s['role'] == 'gate')
        self.assertEqual(step['dependencies'], [first_gate['id']])
        e.answer(rid, step['id'], 'focus')
        r = wait_for(lambda: (x if (x := run_state(e, rid))['status'] == 'completed' else None))
        ids = {s['id'] for s in r['steps']}
        self.assertTrue(all(set(s['dependencies']) <= ids for s in r['steps']))

    def test_event_log_reports_calls_questions_and_answers(self):
        e = Engine(workers=2)
        rid = e.launch('qc', {'source': 'source'})
        r = wait_for(lambda: next((x for x in [run_state(e, rid)] if any(s['status'] == 'waiting' for s in x['steps'])), None))
        event = next(event for event in r['events'] if event['kind'] == 'question')
        self.assertEqual(event['data']['question']['key'], 'focus')
        self.assertGreater(event['data']['usage']['cost_usd'], 0)
        e.answer(rid, event['step_id'], 'clarity')
        r = wait_for(lambda: (x if (x := run_state(e, rid))['status'] == 'completed' else None))
        kinds = [event['kind'] for event in r['events']]
        self.assertEqual(kinds[0], 'run_started')
        self.assertIn('answer_received', kinds)
        self.assertIn('call_started', kinds)
        self.assertIn('call_completed', kinds)
        self.assertEqual(kinds[-1], 'run_completed')
        self.assertEqual([e['seq'] for e in r['events']], list(range(1, len(r['events'])+1)))

    def test_follow_up_chat_preserves_workflow_result_and_records_usage(self):
        class ChatExecutor:
            def run(self, request):
                if 'messages' in request.inputs:
                    return AgentResult({'reply': request.inputs['messages'][-1]['content']}, Usage(5, 5))
                return MockAgent().run(request)
        e = Engine(ChatExecutor(), workers=1)
        rid = e.launch('label', {'documents':['document']})
        r = wait_for(lambda: (x if (x := run_state(e, rid))['status'] == 'completed' else None))
        step = next(s for s in r['steps'] if s['role'] == 'labeller')
        original_output, original_total = r['output'], r['cost_usd']
        e.message(rid, step['id'], 'Why this label?')
        r = wait_for(lambda: next((x for x in [run_state(e, rid)] if any(event['kind']=='chat_completed' for event in x['events'])), None))
        result = next(event for event in r['events'] if event['kind']=='chat_completed')
        self.assertEqual(result['data']['output'], {'reply': 'Why this label?'})
        self.assertEqual(r['output'], original_output)
        self.assertGreater(r['cost_usd'], original_total)

    def test_distribution_rescore_edges_follow_own_revision(self):
        e = Engine(workers=2)
        rid = e.launch('distribution', {'items': list('abcdefgh'), 'target': 'normal'})
        r = wait_for(lambda: (x if (x := run_state(e, rid))['status'] == 'completed' else None))
        by_id = {s['id']: s for s in r['steps']}
        rescored = [s for s in r['steps'] if s['role'] == 'difficulty_scorer' and s['iteration'] > 0]
        self.assertTrue(rescored)
        for step in rescored:
            self.assertEqual(len(step['dependencies']), 1)
            parent = by_id[step['dependencies'][0]]
            self.assertIn(parent['role'], ('hardener', 'softener'))
            self.assertEqual(step['item_index'], parent['item_index'])
            self.assertEqual(step['inputs']['item'], parent['output'].get('item', parent['inputs']['item']))

    def test_distribution_comparisons_gate_each_revision_round(self):
        e = Engine(workers=2)
        rid = e.launch('distribution', {'items': list('abcdefgh'), 'target': 'normal'})
        r = wait_for(lambda: (x if (x := run_state(e, rid))['status'] == 'completed' else None))
        comparisons = [s for s in r['steps'] if s.get('display_name') == 'Compare distribution']
        self.assertEqual(len(comparisons), len(r['output']['history']))
        for step, scores in zip(comparisons, r['output']['history']):
            self.assertEqual(step['output']['actual_counts'], [scores.count(n) for n in range(1, 6)])
            self.assertEqual(step['output']['target_counts'], r['output']['target_counts'])
        by_round = {s['iteration']: s for s in comparisons}
        for step in r['steps']:
            if step['role'] in ('hardener', 'softener'):
                self.assertEqual(step['dependencies'], [by_round[step['iteration']-1]['id']])

    def test_default_has_no_worker_limit_and_demo_has_no_delay(self):
        from unittest.mock import patch
        gate = threading.Barrier(5, timeout=2)
        class ConcurrentAgent:
            def run(self, request):
                if request.role == 'labeller':
                    gate.wait()
                return MockAgent().run(request)
        e = Engine(ConcurrentAgent())
        with patch('engine.time.sleep', side_effect=AssertionError('demo must not add latency')):
            rid = e.launch('label', {'documents': list('abcde')}, demo=True)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                r = run_state(e, rid)
                if r['status'] != 'running':
                    break
            self.assertEqual(r['status'], 'completed', r['error'])
        self.assertIsNone(e.snapshot()['workers'])

    def test_non_convergence_is_not_success(self):
        class Stuck:
            def run(self, request):
                if request.role == 'difficulty_scorer':
                    return AgentResult({'difficulty':3}, Usage(1,1))
                return MockAgent().run(request)
        e = Engine(Stuck())
        rid = e.launch('distribution', {'items':list('abcde')})
        r = wait_for(lambda: (x if (x := run_state(e,rid))['status'] != 'running' else None))
        self.assertEqual(r['status'], 'not_converged')
        self.assertFalse(r['output']['matched'])
        self.assertIn('five', r['error'])

    def test_compute_is_free_and_sessions_are_stable(self):
        e = Engine()
        rid = e.launch('distribution', {'items':list('abcde')})
        r = wait_for(lambda: (x if (x := run_state(e,rid))['status'] == 'completed' else None))
        computed = [s for s in r['steps'] if s['kind']=='compute']
        self.assertTrue(computed)
        self.assertTrue(all(s['attempts']==[] for s in computed))
        self.assertEqual(r['cost_usd'], round(sum(a['cost_usd'] for s in r['steps'] for a in s['attempts']),6))
        rid = e.launch('qc', {'source':'source','reviewers':['qc_reviewer','qc_reviewer']})
        r = wait_for(lambda: next((x for x in [run_state(e,rid)] if any(s['status']=='waiting' for s in x['steps'])),None))
        first = next(s for s in r['steps'] if s['role']=='generator')
        last = next(s for s in r['steps'] if s['status']=='waiting')
        self.assertEqual(first['session_id'],last['session_id'])
        reviewers = [s for s in r['steps'] if s['role']=='qc_reviewer']
        self.assertNotEqual(reviewers[0]['session_id'],reviewers[1]['session_id'])
        self.assertTrue(any(e['kind']=='review_feedback' for e in r['events']))
        e.answer(rid,last['id'],'clarity')
        wait_for(lambda: run_state(e,rid)['status']=='completed')

    def test_new_workflow_can_be_registered_without_engine_changes(self):
        from runtime import WorkflowOutcome
        def custom(ctx, inputs):
            result, sid = ctx.call('custom', inputs, session='custom')
            out, _ = ctx.compute('Wrap', {'value':result}, lambda data: data, dependencies=[sid])
            return WorkflowOutcome(out)
        e=Engine(workflows={'custom':custom})
        rid=e.launch('custom', {'hello':'world'})
        r=wait_for(lambda: (x if (x:=run_state(e,rid))['status']!='running' else None))
        self.assertEqual(r['status'],'completed')
        self.assertEqual(r['output']['value']['role'],'custom')
        self.assertEqual(r['steps'][1]['dependencies'],[r['steps'][0]['id']])
        self.assertNotIn('events',e.summaries()['runs'][0])

    def test_budget_drains_concurrent_calls_and_accounts_all_results(self):
        barrier = threading.Barrier(2, timeout=2)
        class Costly:
            def run(self, request):
                barrier.wait()
                return AgentResult({'label':'x'}, Usage(10,10))
        e = Engine(Costly())
        rid = e.launch('label', {'documents':['a','b']}, run_budget=.000001)
        r = wait_for(lambda: (x if (x:=run_state(e,rid))['status']!='running' else None))
        self.assertEqual(r['status'],'stopped')
        self.assertEqual(sum(len(s['attempts']) for s in r['steps']),2)
        self.assertEqual(r['cost_usd'],.0001)
        self.assertFalse(any(s['role']=='aggregator' for s in r['steps']))

    def test_failure_wakes_waiting_sibling_and_other_runs_continue(self):
        class Mixed:
            def run(self, request):
                from starter.agents import Question
                if request.inputs.get('document')=='wait':
                    return AgentResult({}, Usage(1,1), Question('help','Need help'))
                if request.inputs.get('document')=='fail':
                    raise ValueError('provider failed')
                return MockAgent().run(request)
        e=Engine(Mixed())
        rid=e.launch('label',{'documents':['wait','fail']})
        r=wait_for(lambda: (x if (x:=run_state(e,rid))['status']!='running' else None))
        self.assertEqual(r['status'],'failed')
        self.assertFalse(any(s['status']=='waiting' for s in r['steps']))
        other=e.launch('label',{'documents':['ok']})
        wait_for(lambda: run_state(e,other)['status']=='completed')

    def test_worker_limit_across_runs(self):
        class Slow:
            def __init__(self): self.lock=threading.Lock(); self.active=0; self.peak=0
            def run(self, request):
                with self.lock:
                    self.active+=1; self.peak=max(self.peak,self.active)
                time.sleep(.02)
                result=MockAgent().run(request)
                with self.lock: self.active-=1
                return result
        agent=Slow(); e=Engine(agent,workers=2)
        ids=[e.launch('label', {'documents':list('abcdef')}) for _ in range(3)]
        wait_for(lambda: all(run_state(e,r)['status']=='completed' for r in ids))
        self.assertEqual(agent.peak,2)

    def test_waiting_sibling_does_not_block_independent_step(self):
        class QuestionFirst:
            def run(self, request):
                from starter.agents import Question
                if request.role=='labeller' and request.inputs['document']=='a' and not request.context['answers']:
                    return AgentResult({},Usage(1,1),Question('x','Answer?'))
                return MockAgent().run(request)
        e=Engine(QuestionFirst(),workers=1)
        rid=e.launch('label',{'documents':['a','b']})
        r=wait_for(lambda: next((x for x in [run_state(e,rid)] if any(s['status']=='waiting' for s in x['steps']) and any(s['role']=='labeller' and s['status']=='completed' for s in x['steps'])),None))
        step=next(s for s in r['steps'] if s['status']=='waiting')
        e.answer(rid,step['id'],'yes')
        wait_for(lambda: run_state(e,rid)['status']=='completed')

if __name__=='__main__': unittest.main()
