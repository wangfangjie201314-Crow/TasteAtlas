"""Fail-closed review deployment; never creates a new request allowance."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3

from .preview import PreviewGate


def _existing_state(value):
    path = Path(value)
    if not value or not path.is_absolute():
        raise ValueError('A private absolute state directory is required.')
    path = path.resolve()
    source = Path(__file__).resolve().parent.parent
    if path == source or source in path.parents:
        raise ValueError('Review state must be outside the source tree.')
    database = path / 'guided.sqlite'
    if not database.is_file() or database.is_symlink():
        raise ValueError('The existing review ledger must be mounted before startup.')
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('SELECT id, cap FROM budget').fetchall() != [(1, 12)]:
                raise ValueError('The authorized review ledger must keep its 12-call cap.')
            connection.execute('SELECT id, started, path FROM calls LIMIT 0')
            connection.execute('SELECT ref, expires, state, payload FROM profiles LIMIT 0')
            used = connection.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
            if not 0 <= used <= 12:
                raise ValueError('The review ledger usage is invalid.')
    except sqlite3.Error:
        raise ValueError('The existing review ledger is unavailable or invalid.') from None
    return path


def create_app():
    """Uvicorn factory. Secrets come only from the host's private environment."""
    code = os.environ.get('TASTEATLAS_REVIEW_ACCESS_CODE', '')
    gate = PreviewGate(None, code)
    key = os.environ.get('QLOO_API_KEY', '').strip()
    if not key:
        raise ValueError('A private Qloo event credential is required.')
    state = _existing_state(os.environ.get('TASTEATLAS_STATE_DIR', ''))
    # Set before importing config: dotenv cannot enable the old LLM or local key.
    os.environ.update(
        QLOO_API_KEY=key, QLOO_BASE_URL='https://hackathon.api.qloo.com',
        LLM_API_KEY='', TASTEATLAS_LLM_ENABLED='0', TASTEATLAS_LOCAL_QLOO='0',
        TASTEATLAS_STATE_DIR=str(state), TASTEATLAS_QLOO_CALL_LIMIT='12',
        TASTEATLAS_REQUIRE_EXISTING_STATE='1',
        REQUEST_TIMEOUT_S='20',
    )
    from .config import settings
    if (settings.QLOO_API_KEY != key or settings.QLOO_BASE_URL != 'https://hackathon.api.qloo.com'
            or settings.LLM_ENABLED or settings.LLM_API_KEY or settings.LOCAL_QLOO
            or settings.STATE_DIR != str(state) or settings.QLOO_CALL_LIMIT != 12
            or not settings.REQUIRE_EXISTING_STATE):
        raise ValueError('Review configuration was loaded before the secure entry point.')
    from .main import app
    from .runtime import guided_service
    # Validate the mounted DB through the real runtime before serving requests.
    guided_service().usage()
    gate.app = app
    return gate
