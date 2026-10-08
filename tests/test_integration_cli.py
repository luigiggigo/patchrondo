"""Actual subprocess-based orchestration against tiny synthetic CLI executables.

No subscriptions, external services, or real model calls are involved.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from patchrondo.core import initialize, create_task, run_task
from patchrondo.storage import read_json, save_json
from patchrondo.process import execute as real_execute


FAKE_CLI = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
assert 'Authoritative task requirements' in prompt or 'INDEPENDENT CODE REVIEWER' in prompt
is_claude = pathlib.Path(sys.argv[0]).name == 'claude'
if is_claude:
    reviewer = '--tools' in args and 'Write' not in args[args.index('--tools') + 1]
else:
    reviewer = args[args.index('--sandbox') + 1] == 'read-only'
if reviewer:
    payload = {'verdict':'APPROVED', 'summary':'Reviewed', 'issues':[]}
    if is_claude:
        print(json.dumps({'structured_output':payload}))
    else:
        pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(payload))
else:
    pathlib.Path('feature.txt').write_text('good')
    if is_claude:
        print(json.dumps({'result':'Handoff: feature.txt created'}))
    else:
        pathlib.Path(args[args.index('--output-last-message') + 1]).write_text('Handoff: feature.txt created')
'''


class CLISubprocessTests(unittest.TestCase):
    def test_real_adapters_with_synthetic_commands_and_swapped_roles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo, home, bins = root / 'repo', root / 'home', root / 'bin'
            repo.mkdir(); bins.mkdir()
            for args in [('init', '-q'), ('config', 'user.name', 'T'),
                         ('config', 'user.email', 't@example.com')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            (repo / 'README.md').write_text('# Hi\n')
            for args in [('add', '.'), ('commit', '-qm', 'base')]:
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            initialize(home, repo)
            cfg = read_json(home / 'config.json')
            cfg['tests'] = {'enabled': True, 'trust_acknowledged': True,
                'commands': [[sys.executable, '-c', "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]]}
            save_json(home / 'config.json', cfg)
            for name in ['claude', 'codex']:
                exe = bins / name
                exe.write_text(FAKE_CLI)
                exe.chmod(0o755)
            def synthetic_execute(argv, cwd, **kwargs):
                # Windows cannot execute extensionless shebang scripts. Invoke
                # the same synthetic CLI through Python while keeping real I/O.
                if os.name == 'nt':
                    argv = [sys.executable, str(bins / argv[0]), *argv[1:]]
                return real_execute(argv, cwd, **kwargs)

            with patch.dict(os.environ, {'PATH': str(bins) + os.pathsep + os.environ['PATH']}), \
                 patch('patchrondo.providers.execute', side_effect=synthetic_execute):
                for dev, rev in [('claude','codex'), ('codex','claude')]:
                    task_id, workspace = create_task(home, title='Feature', description='Create good feature.txt',
                                                    acceptance=['Tests pass'], developer=dev, reviewer=rev)
                    state = run_task(home, task_id)
                    self.assertEqual(state['status'], 'done', state.get('last_error'))
                    self.assertTrue((workspace / 'feature.txt').exists())
                    self.assertEqual(state['review']['verdict'], 'APPROVED')


if __name__ == '__main__':
    unittest.main()
