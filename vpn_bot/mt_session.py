"""
Per-server MikroTik API session: serialized async queue, single worker thread,
connection pool reuse, circuit breaker, and executor recovery after timeout.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from vpn_bot.utils import logger

_SESSIONS: dict[str, MikroTikServerSession] = {}
_SESSIONS_LOCK = threading.Lock()


class MikroTikCircuitOpenError(Exception):
    """Raised when too many consecutive failures; router is in cooldown."""

    def __init__(self, seconds_remaining: float):
        self.seconds_remaining = seconds_remaining
        super().__init__(f"MikroTik circuit open for {seconds_remaining:.0f}s")


def _server_session_key(host: str, port: int, username: str) -> str:
    return f"{host}:{port}:{username}"


def _env_int(name: str, default: int) -> int:
    return max(1, int(os.getenv(name, str(default))))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


class MikroTikServerSession:
    """One asyncio lock + one worker thread per MikroTik router."""

    def __init__(self, host: str, port: int, username: str):
        self.host = host
        self.port = port
        self.username = username
        self._async_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"mt-{host}")
        self._consecutive_failures = 0
        self._cooldown_until = 0.0

    @property
    def key(self) -> str:
        return _server_session_key(self.host, self.port, self.username)

    def _circuit_threshold(self) -> int:
        return _env_int("MIKROTIK_CIRCUIT_BREAKER_FAILURES", 5)

    def _circuit_cooldown(self) -> float:
        return _env_float("MIKROTIK_CIRCUIT_BREAKER_COOLDOWN_SEC", 60)

    def check_circuit(self) -> None:
        now = time.monotonic()
        if now < self._cooldown_until:
            raise MikroTikCircuitOpenError(self._cooldown_until - now)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._cooldown_until = 0.0

    def record_failure(self, *, is_auth: bool = False) -> None:
        if is_auth:
            self._consecutive_failures = self._circuit_threshold()
        else:
            self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_threshold():
            self._cooldown_until = time.monotonic() + self._circuit_cooldown()
            logger.warning(
                "MikroTik circuit open for %s:%s (%d failures, cooldown %.0fs)",
                self.host,
                self.port,
                self._consecutive_failures,
                self._circuit_cooldown(),
            )

    def _reset_executor_and_pool(self) -> None:
        from vpn_bot.mikrotik_manager import MikroTikManager
        from vpn_bot.mt_cache import evict_manager_for_key

        MikroTikManager.drop_connection_pool(self.host, self.port, self.username)
        evict_manager_for_key(self.key)
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)
        except Exception:
            pass
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"mt-{self.host}"
        )
        logger.info("Reset MikroTik executor for %s:%s", self.host, self.port)

    async def run(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run sync callable on this server's single worker thread."""
        from vpn_bot.mikrotik_manager import is_mikrotik_auth_error

        self.check_circuit()
        async with self._async_lock:
            loop = asyncio.get_running_loop()
            try:
                future = loop.run_in_executor(
                    self._executor,
                    lambda: func(*args, **kwargs),
                )
                if timeout is not None:
                    return await asyncio.wait_for(future, timeout=timeout)
                return await future
            except asyncio.TimeoutError:
                self._reset_executor_and_pool()
                self.record_failure()
                raise
            except Exception as exc:
                if is_mikrotik_auth_error(exc):
                    self._reset_executor_and_pool()
                    self.record_failure(is_auth=True)
                raise


def get_server_session(server) -> MikroTikServerSession:
    """Return (or create) the session for a Server row or config-backed default."""
    from vpn_bot.config import config

    if server:
        host, port, username = server.host, server.port, server.username
    else:
        host = config.MIKROTIK_HOST
        port = config.MIKROTIK_PORT
        username = config.MIKROTIK_USERNAME

    key = _server_session_key(host, port, username)
    with _SESSIONS_LOCK:
        if key not in _SESSIONS:
            _SESSIONS[key] = MikroTikServerSession(host, port, username)
        return _SESSIONS[key]


async def run_mikrotik_for_server(
    server,
    func: Callable[..., Any],
    *args: Any,
    timeout: float | None = None,
    retries: int | None = None,
    **kwargs: Any,
) -> Any:
    """
    Execute a MikroTik callable with per-server serialization, timeout, and retries.
    Drops pool + replaces executor on timeout; circuit breaker on repeated failures.
    """
    from vpn_bot.mikrotik_manager import (
        MikroTikManager,
        is_mikrotik_auth_error,
        is_mikrotik_transient_error,
    )

    session = get_server_session(server)
    max_retries = retries if retries is not None else _env_int("MIKROTIK_APPLY_RETRIES", 3)
    backoff = _env_float("MIKROTIK_RETRY_BACKOFF_SEC", 2)
    last_exc: Exception | None = None
    op_name = getattr(func, "__name__", repr(func))
    server_label = f"{getattr(server, 'host', '?')}:{getattr(server, 'port', 8728)}"
    logger.info("MikroTik op start %s op=%s", server_label, op_name)

    for attempt in range(1, max_retries + 1):
        try:
            result = await session.run(func, *args, timeout=timeout, **kwargs)
            session.record_success()
            return result
        except MikroTikCircuitOpenError:
            raise
        except asyncio.TimeoutError as exc:
            last_exc = exc
            logger.warning(
                "MikroTik op timeout %s:%s attempt %d/%d",
                session.host,
                session.port,
                attempt,
                max_retries,
            )
        except Exception as exc:
            last_exc = exc
            if is_mikrotik_auth_error(exc):
                MikroTikManager.drop_connection_pool(session.host, session.port, session.username)
                session.record_failure(is_auth=True)
                raise
            if not is_mikrotik_transient_error(exc):
                session.record_failure()
                raise
            logger.warning(
                "MikroTik transient error %s:%s attempt %d/%d: %s",
                session.host,
                session.port,
                attempt,
                max_retries,
                exc,
            )
            session.record_failure()

        if attempt < max_retries:
            MikroTikManager.drop_connection_pool(session.host, session.port, session.username)
            await asyncio.sleep(backoff * (2 ** (attempt - 1)))

    session.record_failure()
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("MikroTik operation failed without exception")
