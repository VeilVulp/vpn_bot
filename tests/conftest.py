"""
Pytest configuration and fixtures for VPN Bot testing.
"""
import pytest
import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import AsyncSessionLocal, init_db
from models import User, Server, Profile, Subscription


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def setup_db():
    """Initialize database once per test session."""
    await init_db()


@pytest.fixture
async def db_session(setup_db):
    """Provide a database session for each test."""
    async with AsyncSessionLocal() as session:
        yield session
        # Rollback any uncommitted changes
        await session.rollback()


@pytest.fixture
def mikrotik_config():
    """MikroTik connection configuration from environment or defaults."""
    return {
        "host": os.getenv("MIKROTIK_HOST", "192.168.88.1"),
        "username": os.getenv("MIKROTIK_USERNAME", "admin"),
        "password": os.getenv("MIKROTIK_PASSWORD", ""),
        "port": int(os.getenv("MIKROTIK_PORT", "8728"))
    }


@pytest.fixture
def mock_telegram_user():
    """Mock Telegram user data."""
    return {
        "id": 12345678,
        "username": "testuser",
        "first_name": "Test",
        "last_name": "User",
        "full_name": "Test User"
    }


@pytest.fixture
def mock_db_user():
    """Create a mock database User object."""
    return User(
        telegram_id=12345678,
        username="testuser",
        full_name="Test User",
        wallet_balance=50.0
    )


@pytest.fixture
def mock_server():
    """Create a mock Server object."""
    return Server(
        name="TestServer",
        host="192.168.88.1",
        username="admin",
        password="test123",
        port=8728
    )


@pytest.fixture
def mock_profile():
    """Create a mock Profile object."""
    return Profile(
        name="TestPlan",
        price=10.0,
        validity_days=30,
        data_limit_gb=10,
        is_active=True
    )
