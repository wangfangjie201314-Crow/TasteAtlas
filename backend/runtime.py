"""Shared local runtime for web and MCP; default live mode remains disabled."""
from functools import lru_cache
from pathlib import Path
from .config import settings
from .credentials import qloo_key
from .guided import GuidedError, GuidedService
from .qloo_client import QlooClient, QlooError


def client_factory(reserve):
    if settings.QLOO_BASE_URL.rstrip('/')!='https://hackathon.api.qloo.com':
        raise QlooError('This event credential is restricted to the official hackathon endpoint.')
    return QlooClient(qloo_key(settings),base_url=settings.QLOO_BASE_URL,
        timeout_s=min(settings.REQUEST_TIMEOUT_S,20),proxy=settings.QLOO_PROXY or None,before_request=reserve,max_elapsed_s=120)


@lru_cache(maxsize=1)
def guided_service():
    if not settings.STATE_DIR:
        raise GuidedError(503,'Guided mode needs a private server state directory before live requests are enabled.')
    return GuidedService(Path(settings.STATE_DIR)/'guided.sqlite',client_factory,settings.QLOO_CALL_LIMIT,
                         require_existing=settings.REQUIRE_EXISTING_STATE)
