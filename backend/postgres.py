"""Optional durable review storage. Runtime never creates or resets a ledger."""
from contextlib import contextmanager
import time

import certifi
import psycopg
from psycopg.conninfo import conninfo_to_dict

from .guided import GuidedError, GuidedService


class _Row(dict):
    """The existing workflow uses both SQLite column names and positions."""
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def _row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: _Row(zip(names, values))


def connection_options(value):
    try:
        options = conninfo_to_dict(value)
        if not value or not options.get('host') or not options.get('dbname'):
            raise ValueError()
        # No libpq service files, shell commands or caller-supplied TLS settings.
        allowed = {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'channel_binding'}
        if set(options) - allowed:
            raise ValueError()
        result = {name: options[name] for name in ('host', 'port', 'dbname', 'user', 'password')
                  if name in options}
        result.update(sslmode='verify-full', sslrootcert=certifi.where(),
                      connect_timeout=15, application_name='tasteatlas-review',
                      options='-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=public')
        return result
    except (ValueError, psycopg.Error):
        raise ValueError('The private PostgreSQL connection configuration is invalid.') from None


@contextmanager
def private_connection(options):
    try:
        with psycopg.connect(**options, row_factory=_row_factory) as connection:
            yield connection
    except psycopg.Error:
        # Never expose the DSN, hostname, password, query parameters or server text.
        raise GuidedError(503, 'The persistent review store is unavailable. No automatic retry was sent.') from None


def validate_ledger(connection):
    budget = connection.execute('SELECT id, cap FROM budget').fetchall()
    if budget != [_Row(id=1, cap=12)]:
        raise ValueError('The existing review ledger must keep its authorized 12-call cap.')
    connection.execute('SELECT id, started, path FROM calls LIMIT 0')
    connection.execute('SELECT ref, expires, state, payload FROM profiles LIMIT 0')
    used = connection.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
    if not 0 <= used <= 12:
        raise ValueError('The existing review ledger usage is invalid.')


class _WorkflowConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        # Only fixed SQL authored in GuidedService passes through this adapter.
        # Reads do not lock: cached plan() reads usage on a second connection.
        if statement == 'BEGIN IMMEDIATE':
            locked = self.connection.execute('SELECT id, cap FROM budget FOR UPDATE').fetchall()
            if locked != [_Row(id=1, cap=12)]:
                raise ValueError('The authorized review budget is unavailable.')
            return self.connection.cursor()
        return self.connection.execute(statement.replace('?', '%s'), parameters)


class PostgresGuidedService(GuidedService):
    def __init__(self, database_url, client_factory, cap=12, clock=time.time):
        if cap != 12:
            raise ValueError('The review request allowance cannot be changed.')
        self.options = connection_options(database_url)
        self.factory = client_factory
        self.clock = clock
        with private_connection(self.options) as connection:
            validate_ledger(connection)

    @contextmanager
    def _connect(self):
        with private_connection(self.options) as connection:
            yield _WorkflowConnection(connection)
