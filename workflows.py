"""Example workflows. Add another function to WORKFLOWS to register a new pipeline."""
from runtime import WorkflowOutcome, WorkflowDefinition


def label(ctx, inputs):
    labels = ctx.parallel([{'role':'labeller', 'inputs':{'document':doc},
                           'session':f'label/{i}'} for i, doc in enumerate(inputs['documents'])])
    output, _ = ctx.call('aggregator', {'labels':[out for out, _ in labels]},
                         dependencies=[sid for _, sid in labels], session='aggregate')
    return WorkflowOutcome(output)


def qc(ctx, inputs):
    findings, answers, dependency = [], {}, []
    for iteration in range(10):
        generated, generator_id = ctx.call('generator', {'source':inputs['source'], 'findings':findings},
            iteration=iteration, dependencies=dependency, answers=answers, session='generator')
        answers = ctx.answers(generator_id)
        reviewers = inputs.get('reviewers', ['qc_reviewer'])
        reviews = ctx.parallel([{'role':role, 'inputs':{'candidates':generated['candidates']},
            'iteration':iteration, 'dependencies':[generator_id], 'session':f'reviewer/{i}'}
            for i, role in enumerate(reviewers)])
        decision, gate_id = ctx.call('gate', {'reviews':[out for out, _ in reviews]},
            iteration=iteration, dependencies=[sid for _, sid in reviews], session='gate')
        if decision['decision'] == 'accept':
            return WorkflowOutcome({'candidates':generated['candidates'], 'gate':decision, 'iterations':iteration+1})
        if decision['decision'] != 'revise':
            raise ValueError('Gate returned an unknown decision')
        findings = [out for out, _ in reviews]
        ctx.feedback(generator_id, [f for out in findings for f in out.get('findings', [])], iteration+1)
        dependency = [gate_id]
    return WorkflowOutcome({'candidates':generated['candidates'], 'gate':decision, 'iterations':10},
                           'not_converged', 'QC reached its 10-round limit without acceptance')


def histogram(scores):
    if any(type(score) is not int or not 1 <= score <= 5 for score in scores):
        raise ValueError('Scorer must return integer difficulty scores in 1..5')
    return [scores.count(level) for level in range(1, 6)]


def distribution(ctx, inputs):
    items = list(inputs['items'])
    target = inputs.get('target', 'uniform')
    scorer = inputs.get('scorer', 'difficulty_scorer')
    weights = [1]*5 if target == 'uniform' else [1,4,6,4,1]
    exact = [len(items)*w/sum(weights) for w in weights]
    counts = [int(value) for value in exact]
    for i in sorted(range(5), key=lambda i:(-(exact[i]-counts[i]), i))[:len(items)-sum(counts)]:
        counts[i] += 1
    desired = [level+1 for level, count in enumerate(counts) for _ in range(count)]
    scored = ctx.parallel([{'role':scorer, 'inputs':{'item':item}, 'item_index':i}
                           for i,item in enumerate(items)])
    current = [out['difficulty'] for out,_ in scored]
    histogram(current)
    assignment = [0]*len(items)
    for index, goal in zip(sorted(range(len(items)),key=lambda i:(current[i],i)),desired):
        assignment[index] = goal
    history, comparisons = [current[:]], []

    def compare(round_number, dependencies):
        def calculate(data):
            actual = histogram(data['scores'])
            return {'target_distribution':target, 'levels':[1,2,3,4,5],
                    'target_counts':data['target_counts'], 'actual_counts':actual,
                    'matched':actual==data['target_counts']}
        out, sid = ctx.compute('Compare distribution', {'scores':current[:], 'target_counts':counts[:]},
                               calculate, iteration=round_number, dependencies=dependencies)
        comparisons.append(out)
        return out, sid

    comparison, compare_id = compare(0,[sid for _,sid in scored])
    for iteration in range(1,6):
        if comparison['matched']:
            break
        changed = [i for i in range(len(items)) if current[i]!=assignment[i]]
        revised = ctx.parallel([{'role':'hardener' if current[i]<assignment[i] else 'softener',
            'inputs':{'item':items[i], 'difficulty':current[i]}, 'iteration':iteration,
            'dependencies':[compare_id], 'item_index':i} for i in changed])
        for i,(out,_) in zip(changed,revised):
            items[i] = out.get('item',items[i])
        scored = ctx.parallel([{'role':scorer, 'inputs':{'item':items[i], 'difficulty':out['difficulty']},
            'iteration':iteration, 'dependencies':[sid], 'item_index':i}
            for i,(out,sid) in zip(changed,revised)])
        for i,(out,_) in zip(changed,scored):
            current[i] = out['difficulty']
        history.append(current[:])
        comparison, compare_id = compare(iteration,[sid for _,sid in scored])
    output = {'target':target, 'target_counts':counts, 'matched':comparison['matched'],
              'items':[{'item':item,'difficulty':score} for item,score in zip(items,current)],
              'history':history,'comparisons':comparisons}
    return WorkflowOutcome(output, 'completed' if comparison['matched'] else 'not_converged',
        None if comparison['matched'] else 'Distribution did not match after five revision rounds')


def validate_list(inputs, key):
    if not isinstance(inputs.get(key), list) or not 1 <= len(inputs[key]) <= 200:
        raise ValueError(f'{key} must be a nonempty list of at most 200 items')


def validate_qc(inputs):
    if not isinstance(inputs.get('source'), str) or not inputs['source'].strip():
        raise ValueError('Source is required')
    roles = inputs.get('reviewers', ['qc_reviewer'])
    if not isinstance(roles,list) or not 1 <= len(roles) <= 8 or any(not isinstance(r,str) or not r.endswith('reviewer') for r in roles):
        raise ValueError('reviewers must contain 1–8 reviewer role names')


def validate_distribution(inputs):
    validate_list(inputs, 'items')
    if inputs.get('target','uniform') not in ('uniform','normal'):
        raise ValueError('Target must be uniform or normal')
    if not isinstance(inputs.get('scorer','difficulty_scorer'),str) or not inputs.get('scorer','difficulty_scorer').strip():
        raise ValueError('scorer must be a nonempty role name')


WORKFLOWS = {
    'label': WorkflowDefinition(label, lambda inputs: validate_list(inputs,'documents')),
    'qc': WorkflowDefinition(qc, validate_qc),
    'distribution': WorkflowDefinition(distribution, validate_distribution),
}
