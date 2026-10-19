"""Key-free process tests for controlled deployment and durable allowance."""
import hashlib
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from backend.guided import GuidedService


BASE = Path(__file__).resolve().parents[1]
CODE = 'fixture-only-' * 4
BOOT = '''
import socket
original_connect = socket.socket.connect
def deny_network(sock, address):
    if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
        return original_connect(sock, address)
    raise AssertionError('No external network is allowed in deployment tests.')
socket.socket.connect = deny_network
from backend.hosted import create_app
'''


class HostedAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tasteatlas-hosted-')
        self.state = Path(self.temp.name) / 'private-state'
        self.database = self.state / 'guided.sqlite'
        service = GuidedService(self.database, None, 12)
        for _ in range(6):
            service.reserve('/fixture-only')

    def tearDown(self):
        self.temp.cleanup()

    def run_child(self, code, **changes):
        env = dict(os.environ)
        env.update(QLOO_API_KEY='fixture-event-key', LLM_API_KEY='',
                   TASTEATLAS_REVIEW_ACCESS_CODE=CODE,
                   TASTEATLAS_STATE_DIR=str(self.state),
                   TASTEATLAS_QLOO_CALL_LIMIT='12', TASTEATLAS_LOCAL_QLOO='0',
                   TASTEATLAS_REQUIRE_EXISTING_STATE='0',
                   TASTEATLAS_DATABASE_URL='',
                   TASTEATLAS_LLM_ENABLED='0', QLOO_PROXY_URL='', PYTHONPATH=str(BASE))
        env.update(changes)
        process = subprocess.run([sys.executable, '-X', 'utf8', '-c', BOOT + code],
                                 cwd=BASE, env=env, capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        return process.stdout.decode('utf-8').strip()

    def reject(self, **changes):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        output = self.run_child('''
try:
    create_app()
except ValueError:
    print('REJECTED')
else:
    raise AssertionError('Invalid deployment started.')
''', **changes)
        self.assertEqual(output, 'REJECTED')
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_missing_secrets_refused_without_changing_ledger(self):
        self.reject(TASTEATLAS_REVIEW_ACCESS_CODE='')
        self.reject(TASTEATLAS_REVIEW_ACCESS_CODE='short')
        self.reject(QLOO_API_KEY='')

    def test_missing_mount_does_not_create_a_new_allowance(self):
        missing = Path(self.temp.name) / 'missing-state'
        self.reject(TASTEATLAS_STATE_DIR=str(missing))
        self.assertFalse(missing.exists())

    def test_relative_and_source_tree_state_are_refused(self):
        for value in ['', 'relative-state', str(BASE), str(BASE / 'backend')]:
            self.reject(TASTEATLAS_STATE_DIR=value)

    def test_changed_cap_and_overconsumed_ledger_are_refused(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE budget SET cap=24')
        self.reject()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE budget SET cap=12')
            connection.executemany('INSERT INTO calls(started,path) VALUES(0,?)',
                                   [('/fixture-only',)] * 7)
        self.reject()

    def test_broken_or_incomplete_database_is_not_reinitialized(self):
        self.database.write_bytes(b'fixture not sqlite')
        self.reject()
        self.database.unlink()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('CREATE TABLE budget(id INTEGER, cap INTEGER)')
            connection.execute('INSERT INTO budget VALUES(1,12)')
        self.reject()

    def test_real_app_is_authenticated_and_old_llm_route_hidden(self):
        output = self.run_child('''
import os
from fastapi.testclient import TestClient
app = create_app()
from backend.config import settings
from backend.runtime import guided_service
assert not settings.LLM_ENABLED and not settings.LLM_API_KEY and not settings.LOCAL_QLOO
assert guided_service().usage() == {'used':6, 'limit':12}
with TestClient(app) as web:
    assert web.get('/').status_code == 401
    auth = ('reviewer', os.environ['TASTEATLAS_REVIEW_ACCESS_CODE'])
    response = web.get('/', auth=auth)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    health = web.get('/api/health', auth=auth).json()
    assert health['guided_enabled'] and not health['llm_enabled'] and not health['llm_configured']
    assert web.post('/api/plan', auth=auth, json={}).status_code == 404
assert guided_service().usage() == {'used':6, 'limit':12}
print('PASS')
''', TASTEATLAS_LLM_ENABLED='1', LLM_API_KEY='fixture-do-not-use',
            TASTEATLAS_LOCAL_QLOO='1', TASTEATLAS_QLOO_CALL_LIMIT='24')
        self.assertEqual(output, 'PASS')

    def test_fresh_process_retains_prior_reservation(self):
        self.assertEqual(self.run_child('''
create_app()
from backend.runtime import guided_service
service = guided_service()
assert service.usage() == {'used':6, 'limit':12}
service.reserve('/fixture-only')
print('PASS')
'''), 'PASS')
        self.assertEqual(self.run_child('''
create_app()
from backend.runtime import guided_service
assert guided_service().usage() == {'used':7, 'limit':12}
print('PASS')
'''), 'PASS')

    def test_stale_unsafe_config_is_refused(self):
        output = self.run_child('''
from backend.config import settings
assert settings.LLM_ENABLED
try:
    create_app()
except ValueError:
    print('REJECTED')
else:
    raise AssertionError('Previously loaded unsafe settings were accepted.')
''', TASTEATLAS_LLM_ENABLED='1')
        self.assertEqual(output, 'REJECTED')

    def test_ledger_disappearing_after_validation_never_recreates_allowance(self):
        self.assertEqual(self.run_child('''
from pathlib import Path
import sqlite3
from unittest.mock import patch
from backend.hosted import _existing_state
def detached(value):
    state = _existing_state(value)
    (state / 'guided.sqlite').unlink()
    return state
with patch('backend.hosted._existing_state', side_effect=detached):
    try:
        create_app()
    except (ValueError, sqlite3.Error):
        pass
    else:
        raise AssertionError('Lost ledger created a new allowance.')
assert not Path(__import__('os').environ['TASTEATLAS_STATE_DIR'], 'guided.sqlite').exists()
print('PASS')
'''), 'PASS')

    def test_ledger_lost_after_startup_returns_failure_without_recreation(self):
        self.assertEqual(self.run_child('''
from pathlib import Path
import os
from fastapi.testclient import TestClient
app = create_app()
database = Path(os.environ['TASTEATLAS_STATE_DIR']) / 'guided.sqlite'
database.unlink()
with TestClient(app) as web:
    response = web.post('/api/tools/state', json={'profile_ref':'x'*32},
                        auth=('reviewer',os.environ['TASTEATLAS_REVIEW_ACCESS_CODE']))
    assert response.status_code == 503
assert not database.exists()
print('PASS')
'''), 'PASS')


if __name__ == '__main__':
    unittest.main()
