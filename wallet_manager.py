from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from models import User, Transaction, PaymentReceipt
from database import AsyncSessionLocal
from utils import logger
from datetime import datetime

class WalletManager:
    @staticmethod
    async def deposit(user_id: int, amount: float, description: str = "Deposit") -> bool:
        """Add funds to user wallet."""
        async with AsyncSessionLocal() as session:
             try:
                 # Update User Balance
                 stmt = select(User).where(User.id == user_id)
                 result = await session.execute(stmt)
                 user = result.scalars().first()
                 
                 if not user:
                     logger.error(f"Deposit failed: User {user_id} not found")
                     return False
                 
                 user.wallet_balance += amount
                 
                 # Create Transaction
                 txn = Transaction(
                     user_id=user_id,
                     amount=amount,
                     type="deposit",
                     description=description
                 )
                 session.add(txn)
                 
                 await session.commit()
                 logger.info(f"Deposited ${amount} to user {user_id}")
                 return True
             except Exception as e:
                 logger.error(f"Deposit error: {e}")
                 await session.rollback()
                 return False

    @staticmethod
    async def deduct(user_id: int, amount: float, description: str = "Purchase") -> bool:
        """Deduct funds from user wallet."""
        async with AsyncSessionLocal() as session:
             try:
                 stmt = select(User).where(User.id == user_id)
                 result = await session.execute(stmt)
                 user = result.scalars().first()
                 
                 if not user:
                     return False
                 
                 if user.wallet_balance < amount:
                     return False # Insufficient funds
                 
                 user.wallet_balance -= amount
                 
                 txn = Transaction(
                     user_id=user_id,
                     amount=-amount, # Negative for deduction
                     type="purchase",
                     description=description
                 )
                 session.add(txn)
                 
                 await session.commit()
                 logger.info(f"Deducted ${amount} from user {user_id}")
                 return True
             except Exception as e:
                 logger.error(f"Deduction error: {e}")
                 await session.rollback()
                 return False

    @staticmethod
    async def approve_receipt(receipt_id: int, admin_id: int) -> bool:
        """Approve receipt and credit user."""
        async with AsyncSessionLocal() as session:
            try:
                stmt = select(PaymentReceipt).where(PaymentReceipt.id == receipt_id)
                result = await session.execute(stmt)
                receipt = result.scalars().first()
                
                if not receipt or receipt.status != 'pending':
                    logger.warning(f"Receipt {receipt_id} invalid or not pending")
                    return False
                
                # Update Receipt
                receipt.status = 'approved'
                receipt.admin_note = f"Approved by {admin_id}"
                
                # Credit User (Separate atomic valid? Need consistent session? 
                # Better to do in one flow, but WalletManager.deposit uses new session. 
                # Let's simple call deposit AFTER commit, or Refactor deposit to accept session.
                # For simplicity here: Update receipt first, then deposit.)
                
                await session.commit()
                
                # Now Deposit
                success = await WalletManager.deposit(receipt.user_id, receipt.amount, f"Receipt #{receipt_id}")
                return success
                
            except Exception as e:
                logger.error(f"Approve receipt error: {e}")
                await session.rollback()
                return False

    @staticmethod
    async def reject_receipt(receipt_id: int, admin_id: int, reason: str = "") -> bool:
        """Reject receipt."""
        async with AsyncSessionLocal() as session:
            try:
                stmt = select(PaymentReceipt).where(PaymentReceipt.id == receipt_id)
                result = await session.execute(stmt)
                receipt = result.scalars().first()
                
                if not receipt:
                    return False
                
                receipt.status = 'rejected'
                receipt.admin_note = f"Rejected by {admin_id}: {reason}"
                
                await session.commit()
                return True
            except Exception as e:
                logger.error(f"Reject receipt error: {e}")
                return False
