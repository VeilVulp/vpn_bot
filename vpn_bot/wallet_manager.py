from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from vpn_bot.models import User, Transaction, PaymentReceipt
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.utils import logger, LanguageManager, get_currency_unit
from datetime import datetime

class WalletManager:
    @staticmethod
    async def deposit(user_id: int, amount: float, description: str = "Deposit") -> bool:
        """Add funds to user wallet."""
        async with AsyncSessionLocal() as session:
             try:
                 # Update User Balance
                 stmt = select(User).where(User.id == user_id).with_for_update()
                 result = await session.execute(stmt)
                 user = result.scalars().first()
                 
                 if not user:
                     logger.error(f"Deposit failed: User {user_id} not found")
                     return False
                 
                 user.wallet_balance += amount
                 
                 # Create Transaction
                 unit = await get_currency_unit()
                 txn = Transaction(
                     user_id=user_id,
                     amount=amount,
                     type="deposit",
                     description=description,
                     currency_unit=unit
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
    async def _deduct_logic(
        session: AsyncSession,
        user_id: int,
        amount: float,
        description: str,
        *,
        discount_code_id: int | None = None,
        original_amount: float | None = None,
        discount_amount: float | None = None,
    ) -> Transaction | None:
        stmt = select(User).where(User.id == user_id).with_for_update()
        result = await session.execute(stmt)
        user = result.scalars().first()
        
        if not user:
            return None
            
        if amount <= 0:
            unit = await get_currency_unit()
            txn = Transaction(
                user_id=user_id,
                amount=0,
                type="purchase",
                description=description,
                currency_unit=unit,
                discount_code_id=discount_code_id,
                original_amount=original_amount,
                discount_amount=discount_amount or 0,
            )
            session.add(txn)
            await session.flush()
            return txn
            
        if user.wallet_balance < amount:
            return None
            
        user.wallet_balance -= amount
        
        unit = await get_currency_unit()
        txn = Transaction(
            user_id=user_id,
            amount=-amount,
            type="purchase",
            description=description,
            currency_unit=unit,
            discount_code_id=discount_code_id,
            original_amount=original_amount,
            discount_amount=discount_amount,
        )
        session.add(txn)
        await session.flush()
        return txn

    @staticmethod
    async def deduct(
        user_id: int,
        amount: float,
        description: str = "Purchase",
        session: AsyncSession = None,
        *,
        discount_code_id: int | None = None,
        original_amount: float | None = None,
        discount_amount: float | None = None,
    ) -> bool:
        """Deduct funds from user wallet. Supports external session for atomicity."""
        if session:
            txn = await WalletManager._deduct_logic(
                session,
                user_id,
                amount,
                description,
                discount_code_id=discount_code_id,
                original_amount=original_amount,
                discount_amount=discount_amount,
            )
            return txn is not None
            
        async with AsyncSessionLocal() as session_internal:
             try:
                 txn = await WalletManager._deduct_logic(
                     session_internal,
                     user_id,
                     amount,
                     description,
                     discount_code_id=discount_code_id,
                     original_amount=original_amount,
                     discount_amount=discount_amount,
                 )
                 if txn:
                     await session_internal.commit()
                     return True
                 return False
             except Exception as e:
                 logger.error(f"Deduction error: {e}")
                 await session_internal.rollback()
                 return False

    @staticmethod
    async def approve_receipt(
        receipt_id: int, admin_id: int, txn_type: str = "deposit_card"
    ) -> bool:
        """Approve receipt and credit user in a single database transaction.

        For plan-linked receipts the credit is capped at what the user's wallet
        actually needs to cover the plan price, preventing over-crediting when
        the balance was already sufficient or a parallel top-up occurred.
        """
        async with AsyncSessionLocal() as session:
            try:
                stmt = (
                    select(PaymentReceipt)
                    .where(PaymentReceipt.id == receipt_id)
                    .with_for_update()
                )
                result = await session.execute(stmt)
                receipt = result.scalars().first()

                if not receipt or receipt.status != 'pending':
                    logger.warning(f"Receipt {receipt_id} invalid or not pending")
                    return False

                user_stmt = select(User).where(User.id == receipt.user_id).with_for_update()
                user_result = await session.execute(user_stmt)
                user = user_result.scalars().first()
                if not user:
                    logger.error(f"Approve receipt: user {receipt.user_id} not found")
                    return False

                receipt.status = 'approved'
                receipt.admin_note = f"Approved by {admin_id}"
                credit = receipt.credit_amount if receipt.credit_amount is not None else receipt.amount

                if receipt.plan_id and receipt.amount is not None:
                    # For plan-linked receipts, credit only what is needed to
                    # cover the stated receipt amount; never credit more than
                    # the shortfall so users cannot gain excess wallet balance.
                    shortfall = max(0.0, float(receipt.amount) - user.wallet_balance)
                    credit = min(float(credit), shortfall)

                user.wallet_balance += credit

                unit = await get_currency_unit()
                from vpn_bot.admin_receipt_service import receipt_display_id

                display_id = receipt_display_id(receipt)
                desc = LanguageManager.get('desc.receipt_approved', id=display_id)
                discount_amt = None
                if receipt.discount_code_id and receipt.payable_amount is not None:
                    discount_amt = max(0.0, credit - receipt.payable_amount)

                txn = Transaction(
                    user_id=receipt.user_id,
                    amount=credit,
                    type=txn_type,
                    description=desc,
                    currency_unit=unit,
                    receipt_id=receipt.id,
                    discount_code_id=receipt.discount_code_id,
                    original_amount=credit if receipt.discount_code_id else None,
                    discount_amount=discount_amt,
                )
                session.add(txn)
                await session.flush()

                if receipt.discount_code_id and receipt.payable_amount is not None and not receipt.plan_id:
                    from vpn_bot.discount_service import redeem_coupon_atomic
                    await redeem_coupon_atomic(
                        session,
                        code_id=receipt.discount_code_id,
                        user_id=user.id,
                        context="wallet_topup",
                        original_amount=credit,
                        final_amount=receipt.payable_amount,
                        discount_amount=discount_amt or 0.0,
                        currency=unit,
                        transaction_id=txn.id,
                        receipt_id=receipt.id,
                    )
                await session.commit()
                logger.info(f"Approved receipt {receipt_id}, credited {receipt.amount} to user {receipt.user_id}")
                return True

            except Exception as e:
                logger.error(f"Approve receipt error: {e}")
                await session.rollback()
                return False

    @staticmethod
    async def reject_receipt(receipt_id: int, admin_id: int, reason: str = "") -> bool:
        """Reject receipt."""
        async with AsyncSessionLocal() as session:
            try:
                stmt = (
                    select(PaymentReceipt)
                    .where(PaymentReceipt.id == receipt_id)
                    .with_for_update()
                )
                result = await session.execute(stmt)
                receipt = result.scalars().first()
                
                if not receipt or receipt.status != "pending":
                    logger.warning(f"Receipt {receipt_id} invalid or not pending for reject")
                    return False

                receipt.status = 'rejected'
                receipt.admin_note = f"Rejected by {admin_id}: {reason}"
                
                await session.commit()
                return True
            except Exception as e:
                logger.error(f"Reject receipt error: {e}")
                return False
