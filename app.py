"""Local-only HTTP API and static UI; Python 3.10+, no dependencies."""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from engine import Engine

ROOT = Path(__file__).parent


def launch_examples(engine):
    """Start independent demonstrations of all workflows."""
    examples = [
        ('label', 'Research evidence review', {'documents': [
            'A randomized study reported reduced symptoms after treatment.',
            'The cohort study tracked outcomes over five years.',
            'Researchers found a correlation between sleep and recovery.',
            'A systematic review compared twelve clinical trials.',
            'A small pilot study suggested further research is needed.',
            'The analysis adjusted for age and prior conditions.',
            'A survey described patient-reported side effects.',
            'A laboratory experiment investigated the mechanism.',
            'The paper discussed limitations in its sample size.',
            'An independent replication reported similar outcomes.']}),
        ('distribution', 'Shape a question bank', {'target':'uniform', 'items': [
            'Define photosynthesis.', 'Explain the role of chlorophyll.',
            'Describe the light-dependent reactions.', 'What is the Calvin cycle?',
            'Compare chloroplasts and mitochondria.', 'How does light intensity affect photosynthesis?',
            'Explain carbon fixation.', 'Predict the effect of water stress.',
            'Compare C3 and C4 pathways.', 'Describe the energy transfer between stages.']}),
        ('qc', 'Generate learning questions', {'reviewers':['accuracy_reviewer','clarity_reviewer'], 'source':
            'Photosynthesis converts light energy into chemical energy. Light-dependent reactions produce ATP and NADPH; the Calvin cycle uses them to fix carbon.'}),
        ('label', 'Classify customer feedback', {'documents': [
            'Setup was straightforward and the guide was clear.',
            'The dashboard loaded slowly on my laptop.',
            'I would like an export option for reports.',
            'The search helped me find my work quickly.',
            'Notifications arrived too late.',
            'The new layout made navigation easier.',
            'I could not find the billing settings.',
            'The support team answered my question quickly.']}),
        ('distribution', 'Balance an assessment', {'target':'normal', 'items': [
            'Identify the variables in an experiment.', 'Interpret a confidence interval.',
            'Explain selection bias.', 'Distinguish correlation from causation.',
            'Evaluate a randomized trial.', 'Design a suitable control group.',
            'Explain statistical power.', 'Assess a potential confounder.']})
    ]
    return [engine.launch(workflow, inputs, name=name, demo=True)
            for workflow, name, inputs in examples]


def serve(engine, port):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/api/state':
                return self.send_json(engine.summaries() if 'summary=1' in urlparse(self.path).query else engine.snapshot())
            if path.startswith('/api/runs/'):
                try:
                    return self.send_json(engine.run_snapshot(path.removeprefix('/api/runs/')))
                except KeyError:
                    return self.send_json({'error':'Run not found'}, 404)
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(path)
            if not name:
                return self.send_error(404)
            body = (ROOT / 'static' / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(name)[0] + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length > 1_000_000:
                    raise ValueError('Request too large')
                data = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == '/api/examples':
                    return self.send_json({'ids': launch_examples(engine)}, 201)
                if path == '/api/runs':
                    return self.send_json({'id': engine.launch(data['workflow'], data['inputs'], data.get('run_budget'), data.get('call_budget'))}, 201)
                if path == '/api/message':
                    engine.message(data['run_id'], data['step_id'], data['message'])
                    return self.send_json({'ok': True})
                if path == '/api/answer':
                    engine.answer(data['run_id'], data['step_id'], data['answer'])
                    return self.send_json({'ok': True})
                self.send_error(404)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self.send_json({'error': str(exc)}, 400)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Open http://127.0.0.1:{port} (instant mock; no worker limit)', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--workers', type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    serve(Engine(), args.port)
