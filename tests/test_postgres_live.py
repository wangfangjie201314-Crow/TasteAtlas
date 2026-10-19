"""Opt-in REAL PostgreSQL integration; never uses Qloo or a paid model.

TASTEATLAS_TEST_PG_ADMIN_URL must name a disposable test cluster whose user may
create databases. TASTEATLAS_TEST_PG_CA can name its test CA. No production
credentials belong here. Each test creates and drops only its unique database.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql

from backend.guided import GuidedError, GuidedService
from backend.migrate_postgres import migrate
from backend.postgres import PostgresGuidedService, connection_options, private_connection
from tests import test_guided as guided_fixture


@unittest.skipUnless(os.environ.get('TASTEATLAS_TEST_PG_ADMIN_URL'), 'No disposable real PostgreSQL test cluster configured.')
class PostgresLiveAcceptance(unittest.TestCase):
    def setUp(self):
        ca = os.environ.get('TASTEATLAS_TEST_PG_CA')
        if ca:
            self.ca_patch = patch('backend.postgres.certifi.where', return_value=ca)
            self.ca_patch.start()
            self.addCleanup(self.ca_patch.stop)
        self.admin_options = connection_options(os.environ['TASTEATLAS_TEST_PG_ADMIN_URL'])
        self.name = 'tasteatlas_test_' + uuid.uuid4().hex
        with psycopg.connect(**self.admin_options, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)))
        self.addCleanup(self.drop_database)
        options = dict(self.admin_options, dbname=self.name)
        self.url = psycopg.conninfo.make_conninfo(**{k: options[k] for k in ('host', 'port', 'dbname', 'user', 'password') if k in options})
        self.temp = tempfile.TemporaryDirectory(prefix='tasteatlas-real-pg-')
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'guided.sqlite'
        old = GuidedService(self.source, None, 12)
        for _ in range(6): old.reserve('/historical-fixture')
        self.now = 1000

    def drop_database(self):
        with psycopg.connect(**self.admin_options, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(self.name)))

    def transfer(self):
        return migrate(self.source, self.url, source_stopped=True)

    def service(self, factory=None):
        return PostgresGuidedService(self.url, factory, clock=lambda: self.now)

    def test_exact_transfer_and_second_transfer_refuses_without_reset(self):
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute('INSERT INTO profiles VALUES(?,?,?,?)',
                               ('private-fixture-ref', 1900, 'ready', '{"fixture":true}'))
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.transfer()
        self.assertEqual((result['used'], result['limit'], result['profiles']), (6, 12, 1))
        self.assertEqual(result['source_sha256'], before)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        with self.assertRaises(ValueError): self.transfer()
        self.assertEqual(self.service().usage(), {'used': 6, 'limit': 12})

    def test_parallel_reservations_stop_at_the_same_permanent_cap(self):
        self.transfer()
        services = [self.service(), self.service()]
        def reserve(index):
            try:
                services[index % 2].reserve('/fixture')
                return 'reserved'
            except GuidedError as error:
                # Bounded lock waits may safely refuse overload with 503.
                # Both refusal paths must send no Qloo and create no allowance.
                self.assertIn(error.status, (429, 503))
                return 'quota_refused' if error.status == 429 else 'busy_refused'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(results.count('reserved'), 6)
        self.assertEqual(len(results), 20)
        self.assertEqual(self.service().usage(), {'used': 12, 'limit': 12})

    def test_real_workflow_cache_and_reconnection_do_not_deadlock_or_call_again(self):
        self.transfer()
        fixture = guided_fixture.GuidedAcceptance(methodName='test_full_confirmation_plan_cache_and_restart')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        service = self.service(fixture.factory)
        resolved = service.resolve('Austin', 'Radiohead')
        result = service.plan(resolved['profile_ref'], 'seed')
        self.assertEqual(result['usage'], {'used': 12, 'limit': 12})
        self.assertEqual(len(result['stops']), 3)
        restarted = self.service(fixture.factory)
        self.assertTrue(restarted.plan(resolved['profile_ref'], 'seed')['from_cache'])
        self.assertEqual(restarted.state(resolved['profile_ref'])['state'], 'complete')
        self.assertEqual(len(fixture.requests), 6)
        self.assertTrue(all(c._client.is_closed for c in fixture.clients))
        with self.assertRaises(GuidedError) as error: restarted.resolve('Austin', 'Radiohead')
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(len(fixture.requests), 6)

    def test_lost_table_fails_without_creating_a_replacement_allowance(self):
        self.transfer()
        service = self.service()
        with private_connection(connection_options(self.url)) as connection:
            connection.execute('DROP TABLE calls')
        with self.assertRaises(GuidedError): service.reserve('/fixture')
        with self.assertRaises(GuidedError): self.service()
        with private_connection(connection_options(self.url)) as connection:
            self.assertIsNone(connection.execute("SELECT to_regclass('public.calls')").fetchone()[0])

    def test_empty_database_is_not_initialized_by_runtime(self):
        with self.assertRaises(GuidedError): self.service()
        with private_connection(connection_options(self.url)) as connection:
            self.assertEqual(connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall(), [])

    def test_failed_transfer_rolls_back_schema_and_all_rows(self):
        with patch('backend.migrate_postgres.validate_ledger', side_effect=ValueError('fixture rejection')):
            with self.assertRaises(ValueError): self.transfer()
        with private_connection(connection_options(self.url)) as connection:
            self.assertEqual(connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall(), [])

    def test_objects_in_any_user_schema_refuse_transfer_without_ddl(self):
        statements = ['CREATE SCHEMA fixture_other', 'CREATE SCHEMA pgfixture',
                      'CREATE VIEW fixture_view AS SELECT 1 AS fixture',
                      'CREATE SEQUENCE fixture_sequence',
                      "CREATE FUNCTION fixture_function() RETURNS integer LANGUAGE sql AS 'SELECT 1'"]
        removals = ['DROP SCHEMA fixture_other', 'DROP SCHEMA pgfixture', 'DROP VIEW fixture_view',
                    'DROP SEQUENCE fixture_sequence', 'DROP FUNCTION fixture_function()']
        for create, remove in zip(statements, removals):
            with self.subTest(create=create):
                with private_connection(connection_options(self.url)) as connection:
                    connection.execute(create)
                with self.assertRaises(ValueError): self.transfer()
                with private_connection(connection_options(self.url)) as connection:
                    self.assertIsNone(connection.execute("SELECT to_regclass('public.budget')").fetchone()[0])
                    connection.execute(remove)

    def test_expired_and_interrupted_profiles_never_retry(self):
        self.transfer()
        payload = json.dumps({'started': 800, 'matches': [], 'city': 'fixture', 'favorite': 'fixture'})
        with private_connection(connection_options(self.url)) as connection:
            connection.execute('INSERT INTO profiles VALUES(%s,%s,%s,%s)', ('fixture', 1900, 'planning', payload))
        service = self.service()
        self.assertEqual(service.state('fixture')['state'], 'failed')
        self.now = 2000
        with self.assertRaises(GuidedError) as error: service.state('fixture')
        self.assertEqual(error.exception.status, 410)
        self.assertEqual(service.usage(), {'used': 6, 'limit': 12})


if __name__ == '__main__': unittest.main()
