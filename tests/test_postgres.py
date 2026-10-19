"""Key-free PostgreSQL safety checks; real server tests are a separate suite."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg

from backend.guided import GuidedError, GuidedService
from backend.migrate_postgres import migrate
from backend.postgres import PostgresGuidedService, connection_options, private_connection


class PostgresSafety(unittest.TestCase):
    def test_external_tls_cannot_be_disabled_by_the_connection_string(self):
        options = connection_options('postgresql://fixture:private-fixture@db.example/review?sslmode=disable')
        self.assertEqual(options['sslmode'], 'verify-full')
        self.assertTrue(Path(options['sslrootcert']).is_file())
        self.assertEqual(options['connect_timeout'], 15)
        self.assertIn('statement_timeout=10000', options['options'])

    def test_invalid_or_service_file_connections_are_refused_without_echoing_values(self):
        for value in ['', 'bad-private-fixture', 'service=private-fixture',
                      'host=db.example dbname=review options=private-fixture']:
            with self.assertRaises(ValueError) as error:
                connection_options(value)
            self.assertNotIn('private-fixture', str(error.exception))

    def test_driver_errors_do_not_expose_server_or_credential_details(self):
        with patch('backend.postgres.psycopg.connect', side_effect=psycopg.OperationalError('private-fixture')):
            with self.assertRaises(GuidedError) as error:
                with private_connection({}):
                    self.fail('A failed connection was yielded.')
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn('private-fixture', str(error.exception))

    def test_missing_schema_does_not_issue_ddl_or_fallback_to_sqlite(self):
        statements = []
        class Missing:
            def execute(self, sql):
                statements.append(sql)
                raise psycopg.errors.UndefinedTable('private-fixture')
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch('backend.postgres.psycopg.connect', return_value=Missing()):
            with self.assertRaises(GuidedError):
                PostgresGuidedService('postgresql://fixture@db.example/review', None)
        self.assertEqual(statements, ['SELECT id, cap FROM budget'])

    def test_migration_refuses_running_empty_and_incompatible_sources_before_network(self):
        with tempfile.TemporaryDirectory(prefix='tasteatlas-pg-safety-') as directory:
            path = Path(directory) / 'review.sqlite'
            service = GuidedService(path, None, 12)
            with patch('backend.migrate_postgres.private_connection') as network:
                with self.assertRaises(ValueError): migrate(path, '', source_stopped=False)
                with self.assertRaises(ValueError): migrate(path, '', source_stopped=True)
                network.assert_not_called()
            service.reserve('/fixture')
            wrong = Path(directory) / 'wrong.sqlite'
            GuidedService(wrong, None, 24).reserve('/fixture')
            with patch('backend.migrate_postgres.private_connection') as network:
                with self.assertRaises(ValueError): migrate(wrong, '', source_stopped=True)
                network.assert_not_called()

    def test_changed_cap_is_rejected_before_connecting(self):
        with patch('backend.postgres.psycopg.connect') as network:
            with self.assertRaises(ValueError):
                PostgresGuidedService('postgresql://fixture@db.example/review', None, 24)
            network.assert_not_called()


if __name__ == '__main__': unittest.main()
