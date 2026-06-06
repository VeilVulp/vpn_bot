"""MikroTik per-server session: retry, circuit breaker, executor recovery."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from vpn_bot.mikrotik_manager import MikroTikManager, is_mikrotik_auth_error
from vpn_bot.mt_session import (
    MikroTikCircuitOpenError,
    MikroTikServerSession,
    get_server_session,
    run_mikrotik_for_server,
)


def test_is_mikrotik_auth_error_detects_login_failure():
    assert is_mikrotik_auth_error(Exception("api login failure for user"))
    assert not is_mikrotik_auth_error(Exception("connection reset by peer"))


def test_connect_with_retry_succeeds_on_second_attempt():
    mgr = MikroTikManager(host="10.0.0.1", username="u", password="p", port=8728, use_pool=False)
    calls = {"n": 0}

    def fake_connect():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("reset")

    with patch.object(mgr, "connect", side_effect=fake_connect):
        with patch("time.sleep"):
            mgr.connect_with_retry(max_attempts=3, base_delay=0.01)
    assert calls["n"] == 2


def test_connect_no_retry_on_auth_failure():
    mgr = MikroTikManager(host="10.0.0.1", username="u", password="p", port=8728, use_pool=False)

    with patch.object(mgr, "connect", side_effect=Exception("invalid user")):
        with patch.object(MikroTikManager, "drop_connection_pool") as drop:
            with pytest.raises(Exception, match="invalid user"):
                mgr.connect_with_retry(max_attempts=3, base_delay=0.01)
            drop.assert_called_once()


def test_circuit_breaker_blocks_after_threshold(monkeypatch):
    monkeypatch.setenv("MIKROTIK_CIRCUIT_BREAKER_FAILURES", "2")
    monkeypatch.setenv("MIKROTIK_CIRCUIT_BREAKER_COOLDOWN_SEC", "30")
    session = MikroTikServerSession("10.0.0.1", 8728, "admin")
    session.record_failure()
    session.record_failure()
    with pytest.raises(MikroTikCircuitOpenError):
        session.check_circuit()


@pytest.mark.asyncio
async def test_run_mikrotik_for_server_replaces_executor_on_timeout(monkeypatch):
    monkeypatch.setenv("MIKROTIK_APPLY_RETRIES", "1")
    server = MagicMock(host="10.0.0.2", port=8728, username="admin")
    session = get_server_session(server)
    old_executor = session._executor

    def slow():
        time.sleep(2)

    with pytest.raises(asyncio.TimeoutError):
        await session.run(slow, timeout=0.05)

    assert session._executor is not old_executor


@pytest.mark.asyncio
async def test_run_mikrotik_for_server_retries_transient(monkeypatch):
    monkeypatch.setenv("MIKROTIK_APPLY_RETRIES", "3")
    monkeypatch.setenv("MIKROTIK_RETRY_BACKOFF_SEC", "0")
    server = MagicMock(host="10.0.0.3", port=8728, username="admin")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("reset")
        return "ok"

    with patch.object(MikroTikManager, "drop_connection_pool"):
        result = await run_mikrotik_for_server(server, flaky, timeout=5, retries=3)
    assert result == "ok"
    assert calls["n"] == 3


def test_list_reader_uses_pool():
    server = MagicMock(host="1.2.3.4", port=8728, username="u", password="p")
    from vpn_bot.mikrotik_manager import get_mikrotik_list_reader

    mgr = get_mikrotik_list_reader(server)
    assert mgr.use_pool is True
