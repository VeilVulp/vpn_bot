"""Load-test fixtures: mock MikroTik with configurable latency."""

import pytest

from tests.conftest_db import *  # noqa: F401,F403
from tests.helpers.mock_mikrotik import MockMikrotikManager, patch_get_mikrotik_manager

pytestmark = pytest.mark.load


@pytest.fixture
def mock_mikrotik_slow(monkeypatch):
    """50–200ms latency to stress pool under concurrent load."""
    patch_get_mikrotik_manager(monkeypatch)

    _orig_for_server = MockMikrotikManager.for_server

    def _for_server(server_id: int):
        m = _orig_for_server(server_id)
        m.latency_ms = (50, 200)
        return m

    monkeypatch.setattr(MockMikrotikManager, "for_server", staticmethod(_for_server))
    yield
