"""
Database Model Tests

Tests for verifying database model operations and relationships.
"""
import pytest
from datetime import datetime, timedelta
from sqlalchemy import select

# Mark all tests as unit tests
pytestmark = pytest.mark.unit


class TestUserModel:
    """Test User model operations."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session):
        """Test user creation in database."""
        from models import User
        
        user = User(
            telegram_id=99999001,
            username="db_test_user",
            full_name="Database Test User",
            wallet_balance=0.0
        )
        db_session.add(user)
        await db_session.commit()
        
        # Verify
        result = await db_session.execute(
            select(User).where(User.telegram_id == 99999001)
        )
        found = result.scalars().first()
        
        assert found is not None
        assert found.username == "db_test_user"
        assert found.wallet_balance == 0.0
        
        # Cleanup
        await db_session.delete(found)
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_user_wallet_update(self, db_session):
        """Test updating user wallet balance."""
        from models import User
        
        user = User(
            telegram_id=99999002,
            username="wallet_test",
            wallet_balance=100.0
        )
        db_session.add(user)
        await db_session.commit()
        
        # Update balance
        user.wallet_balance -= 25.0
        await db_session.commit()
        
        # Verify
        await db_session.refresh(user)
        assert user.wallet_balance == 75.0
        
        # Cleanup
        await db_session.delete(user)
        await db_session.commit()


class TestServerModel:
    """Test Server model operations."""

    @pytest.mark.asyncio
    async def test_create_server(self, db_session):
        """Test server creation with encrypted password."""
        from models import Server
        
        server = Server(
            name="TestServer_DB",
            host="10.0.0.1",
            username="admin",
            password="secret_password",
            port=8728
        )
        db_session.add(server)
        await db_session.commit()
        
        # Verify
        result = await db_session.execute(
            select(Server).where(Server.name == "TestServer_DB")
        )
        found = result.scalars().first()
        
        assert found is not None
        assert found.host == "10.0.0.1"
        # Password should be accessible via property (decrypted)
        assert found.password == "secret_password"
        # Internal storage should be encrypted
        assert found._password != "secret_password"
        
        # Cleanup
        await db_session.delete(found)
        await db_session.commit()


class TestSubscriptionModel:
    """Test Subscription model and relationships."""

    @pytest.mark.asyncio
    async def test_create_subscription_with_relations(self, db_session):
        """Test subscription creation with foreign key relationships."""
        from models import User, Server, Profile, Subscription
        
        # Create dependencies
        user = User(telegram_id=99999010, username="sub_test_user")
        server = Server(
            name="SubTestServer",
            host="10.0.0.2",
            username="admin",
            password="test"
        )
        db_session.add_all([user, server])
        await db_session.flush()
        
        profile = Profile(
            name="SubTestPlan",
            price=15.0,
            validity_days=30,
            data_limit_gb=20,
            server_id=server.id
        )
        db_session.add(profile)
        await db_session.flush()
        
        # Create subscription
        sub = Subscription(
            user_id=user.id,
            server_id=server.id,
            profile_id=profile.id,
            mikrotik_username="mt_sub_test",
            mikrotik_password="mt_pass_123",
            expiry_date=datetime.now() + timedelta(days=30),
            total_limit_bytes=20 * 1024**3
        )
        db_session.add(sub)
        await db_session.commit()
        
        # Verify relationships
        await db_session.refresh(sub)
        assert sub.user.telegram_id == 99999010
        assert sub.server.name == "SubTestServer"
        assert sub.profile.name == "SubTestPlan"
        
        # Cleanup
        await db_session.delete(sub)
        await db_session.delete(profile)
        await db_session.delete(server)
        await db_session.delete(user)
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_subscription_expiry_check(self, db_session):
        """Test subscription expiry date validation."""
        from models import User, Server, Profile, Subscription
        
        # Create minimal deps
        user = User(telegram_id=99999011, username="expiry_test")
        server = Server(name="ExpiryServer", host="10.0.0.3", username="a", password="b")
        db_session.add_all([user, server])
        await db_session.flush()
        
        profile = Profile(
            name="ExpiryPlan",
            price=10.0,
            validity_days=30,
            data_limit_gb=10,
            server_id=server.id
        )
        db_session.add(profile)
        await db_session.flush()
        
        # Create expired subscription
        sub = Subscription(
            user_id=user.id,
            server_id=server.id,
            profile_id=profile.id,
            mikrotik_username="mt_expired",
            mikrotik_password="pass",
            expiry_date=datetime.now() - timedelta(days=5),  # 5 days ago
            total_limit_bytes=10 * 1024**3
        )
        db_session.add(sub)
        await db_session.commit()
        
        # Check expiry
        is_expired = sub.expiry_date < datetime.now()
        assert is_expired is True
        
        # Cleanup
        await db_session.delete(sub)
        await db_session.delete(profile)
        await db_session.delete(server)
        await db_session.delete(user)
        await db_session.commit()


class TestTransactionModel:
    """Test Transaction model for wallet operations."""

    @pytest.mark.asyncio
    async def test_create_transaction(self, db_session):
        """Test transaction record creation."""
        from models import User, Transaction
        
        user = User(telegram_id=99999020, username="txn_test", wallet_balance=100.0)
        db_session.add(user)
        await db_session.flush()
        
        # Record a purchase
        txn = Transaction(
            user_id=user.id,
            amount=-25.0,
            type='purchase',
            description='Test VPN purchase'
        )
        db_session.add(txn)
        await db_session.commit()
        
        # Verify
        await db_session.refresh(txn)
        assert txn.amount == -25.0
        assert txn.type == 'purchase'
        assert txn.created_at is not None
        
        # Cleanup
        await db_session.delete(txn)
        await db_session.delete(user)
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_transaction_types(self, db_session):
        """Test various transaction types."""
        from models import User, Transaction
        
        user = User(telegram_id=99999021, username="txn_types_test")
        db_session.add(user)
        await db_session.flush()
        
        tx_types = ['deposit', 'purchase', 'refund', 'manual_adjustment']
        transactions = []
        
        for tx_type in tx_types:
            txn = Transaction(
                user_id=user.id,
                amount=10.0 if tx_type in ['deposit', 'refund'] else -10.0,
                type=tx_type,
                description=f'Test {tx_type}'
            )
            transactions.append(txn)
            db_session.add(txn)
        
        await db_session.commit()
        
        # Verify all types
        result = await db_session.execute(
            select(Transaction).where(Transaction.user_id == user.id)
        )
        found = result.scalars().all()
        assert len(found) == 4
        
        # Cleanup
        for txn in transactions:
            await db_session.delete(txn)
        await db_session.delete(user)
        await db_session.commit()


class TestTicketModel:
    """Test Ticket model for support system."""

    @pytest.mark.asyncio
    async def test_create_ticket_with_messages(self, db_session):
        """Test ticket creation with message thread."""
        from models import User, Ticket, TicketMessage
        import time
        
        # Use unique ID to avoid conflicts
        unique_id = int(time.time() * 1000) % 9999999999
        
        user = User(telegram_id=unique_id, username=f"ticket_test_{unique_id}")
        db_session.add(user)
        await db_session.flush()
        
        ticket = Ticket(
            user_id=user.id,
            subject="Test connectivity issue",
            status='open',
            priority='medium'
        )
        db_session.add(ticket)
        await db_session.flush()
        
        # Add messages
        msg1 = TicketMessage(
            ticket_id=ticket.id,
            sender_type='user',
            sender_id=unique_id,
            message="I cannot connect to VPN"
        )
        msg2 = TicketMessage(
            ticket_id=ticket.id,
            sender_type='admin',
            sender_id=123456,
            message="Please try restarting your device"
        )
        db_session.add_all([msg1, msg2])
        await db_session.commit()
        
        # Verify using explicit query (async SQLAlchemy doesn't support lazy loading)
        result = await db_session.execute(
            select(TicketMessage).where(TicketMessage.ticket_id == ticket.id).order_by(TicketMessage.id)
        )
        messages = result.scalars().all()
        
        assert len(messages) == 2
        assert messages[0].sender_type == 'user'
        assert messages[1].sender_type == 'admin'
        
        # Cleanup
        await db_session.delete(msg1)
        await db_session.delete(msg2)
        await db_session.delete(ticket)
        await db_session.delete(user)
        await db_session.commit()


