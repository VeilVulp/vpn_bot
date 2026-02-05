"""
Wallet Manager Tests

Tests for wallet operations including deposits, deductions, and receipt approval.
"""
import pytest
from sqlalchemy import select

pytestmark = pytest.mark.unit


class TestWalletDeductions:
    """Test wallet deduction operations."""

    @pytest.mark.asyncio
    async def test_deduct_sufficient_balance(self, db_session):
        """Test deduction with sufficient balance succeeds."""
        from models import User
        from wallet_manager import WalletManager
        
        user = User(
            telegram_id=88880001,
            username="deduct_test",
            wallet_balance=100.0
        )
        db_session.add(user)
        await db_session.commit()
        
        try:
            success = await WalletManager.deduct(user.id, 50.0, "Test deduction")
            
            assert success is True
            
            # Verify new balance
            await db_session.refresh(user)
            assert user.wallet_balance == 50.0
        finally:
            await db_session.delete(user)
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_deduct_insufficient_balance(self, db_session):
        """Test deduction fails with insufficient balance."""
        from models import User
        from wallet_manager import WalletManager
        
        user = User(
            telegram_id=88880002,
            username="insufficient_test",
            wallet_balance=10.0
        )
        db_session.add(user)
        await db_session.commit()
        
        try:
            success = await WalletManager.deduct(user.id, 50.0, "Should fail")
            
            assert success is False
            
            # Balance should be unchanged
            await db_session.refresh(user)
            assert user.wallet_balance == 10.0
        finally:
            await db_session.delete(user)
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_deduct_exact_balance(self, db_session):
        """Test deducting exact balance amount."""
        from models import User
        from wallet_manager import WalletManager
        
        user = User(
            telegram_id=88880003,
            username="exact_test",
            wallet_balance=25.0
        )
        db_session.add(user)
        await db_session.commit()
        
        try:
            success = await WalletManager.deduct(user.id, 25.0, "Exact amount")
            
            assert success is True
            await db_session.refresh(user)
            assert user.wallet_balance == 0.0
        finally:
            await db_session.delete(user)
            await db_session.commit()


class TestReceiptApproval:
    """Test receipt approval workflow."""

    @pytest.mark.asyncio
    async def test_approve_receipt_adds_balance(self, db_session):
        """Test approved receipt adds to wallet balance."""
        from models import User, PaymentReceipt
        from wallet_manager import WalletManager
        
        user = User(
            telegram_id=88880010,
            username="receipt_test",
            wallet_balance=0.0
        )
        db_session.add(user)
        await db_session.flush()
        
        receipt = PaymentReceipt(
            user_id=user.id,
            amount=50.0,
            receipt_file_id="test_file_id_12345",
            status='pending'
        )
        db_session.add(receipt)
        await db_session.commit()
        
        try:
            success = await WalletManager.approve_receipt(receipt.id, admin_id=123456)
            
            assert success is True
            
            # Verify balance updated
            await db_session.refresh(user)
            assert user.wallet_balance == 50.0
            
            # Verify receipt status
            await db_session.refresh(receipt)
            assert receipt.status == 'approved'
        finally:
            await db_session.delete(receipt)
            await db_session.delete(user)
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_reject_receipt(self, db_session):
        """Test rejected receipt doesn't add balance."""
        from models import User, PaymentReceipt
        from wallet_manager import WalletManager
        
        user = User(
            telegram_id=88880011,
            username="reject_test",
            wallet_balance=0.0
        )
        db_session.add(user)
        await db_session.flush()
        
        receipt = PaymentReceipt(
            user_id=user.id,
            amount=100.0,
            receipt_file_id="test_reject_file",
            status='pending'
        )
        db_session.add(receipt)
        await db_session.commit()
        
        try:
            success = await WalletManager.reject_receipt(receipt.id, admin_id=123456)
            
            assert success is True
            
            # Balance should remain 0
            await db_session.refresh(user)
            assert user.wallet_balance == 0.0
            
            # Receipt should be rejected
            await db_session.refresh(receipt)
            assert receipt.status == 'rejected'
        finally:
            await db_session.delete(receipt)
            await db_session.delete(user)
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_approve_nonexistent_receipt(self, db_session):
        """Test approving non-existent receipt fails gracefully."""
        from wallet_manager import WalletManager
        
        success = await WalletManager.approve_receipt(99999999, admin_id=123)
        
        assert success is False


class TestTransactionRecording:
    """Test transaction history recording."""

    @pytest.mark.asyncio
    async def test_deduction_creates_transaction(self, db_session):
        """Test that deductions create transaction records."""
        from models import User, Transaction
        from wallet_manager import WalletManager
        
        user = User(
            telegram_id=88880020,
            username="txn_record_test",
            wallet_balance=100.0
        )
        db_session.add(user)
        await db_session.commit()
        
        try:
            await WalletManager.deduct(user.id, 30.0, "Test purchase")
            
            # Check transaction was created
            result = await db_session.execute(
                select(Transaction).where(Transaction.user_id == user.id)
            )
            txns = result.scalars().all()
            
            assert len(txns) >= 1
            latest_txn = txns[-1]
            assert latest_txn.amount == -30.0
            assert latest_txn.type == 'purchase'
        finally:
            # Cleanup transactions first
            result = await db_session.execute(
                select(Transaction).where(Transaction.user_id == user.id)
            )
            for txn in result.scalars().all():
                await db_session.delete(txn)
            await db_session.delete(user)
            await db_session.commit()
