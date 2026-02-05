
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User as TagUser, Message, CallbackQuery, Chat, KeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from datetime import datetime
from sqlalchemy import select

from models import User

# Import modules to patch
import bot_handler
import user_features
import support_tickets

@pytest.fixture
def update_message(mock_telegram_user):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock(spec=TagUser)
    update.effective_user.id = mock_telegram_user["id"]
    update.effective_user.username = mock_telegram_user["username"]
    update.effective_user.full_name = mock_telegram_user["full_name"]
    update.effective_chat = MagicMock(spec=Chat)
    update.effective_chat.id = mock_telegram_user["id"]
    
    update.message = MagicMock(spec=Message)
    update.message.text = "/start"
    update.callback_query = None
    return update

@pytest.fixture
def update_callback(mock_telegram_user):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock(spec=TagUser)
    update.effective_user.id = mock_telegram_user["id"]
    update.callback_query = MagicMock(spec=CallbackQuery)
    update.callback_query.id = "query_id"
    update.callback_query.data = "some_data"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    return update

@pytest.fixture
def context():
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.user_data = {}
    return ctx

@pytest.fixture
def session_patch(db_session):
    # Create a wrapper that works as an async context manager
    class SessionContextManager:
        def __init__(self):
            self.session = db_session
        async def __aenter__(self):
            return self.session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    # Patch the AsyncSessionLocal in all relevant modules
    mock_factory = MagicMock(return_value=SessionContextManager())
    
    with patch('bot_handler.AsyncSessionLocal', mock_factory), \
         patch('user_features.AsyncSessionLocal', mock_factory), \
         patch('support_tickets.AsyncSessionLocal', mock_factory):
        yield

@pytest.mark.asyncio
async def test_start_command(update_message, context, session_patch, db_session):
    # Test /start command - Should create/get user and show menu
    from bot_handler import start
    await start(update_message, context)
    
    # Check if user created
    async with db_session as session:
        # We need to flush if the session was used in start() but not committed (it usually commits)
        # Since we are sharing the session, changes should be visible
        res = await session.execute(select(User).where(User.telegram_id == update_message.effective_user.id))
        user = res.scalars().first()
        assert user is not None
        assert user.telegram_id == update_message.effective_user.id

    # Verify reply keyboard sent
    update_message.message.reply_text.assert_called_once()
    args = update_message.message.reply_text.call_args
    assert "Welcome" in args[0][0] or "Welcome" in str(args.kwargs.get('text', ''))
    assert args.kwargs.get('reply_markup') is not None
    
    # Check menu items specifically
    keyboard = args.kwargs['reply_markup'].keyboard
    # Helper to get text from potential KeyboardButton objects or strings
    def get_text(btn):
        return btn.text if isinstance(btn, KeyboardButton) else btn

    flat_keys = [str(get_text(btn)) for row in keyboard for btn in row]
    # Check substrings to avoid emoji encoding issues
    assert any("My Subscriptions" in k for k in flat_keys)
    assert any("Tutorials" in k for k in flat_keys)
    assert any("Buy Service" in k for k in flat_keys)
    assert any("History" in k for k in flat_keys)
    assert any("Support" in k for k in flat_keys)
    assert any("Wallet" in k for k in flat_keys)
    assert not any("Settings" in k for k in flat_keys)

@pytest.mark.asyncio
async def test_my_subscriptions_handler(update_message, context, session_patch, db_session, mock_db_user):
    # Ensure user exists (merge if using shared session to avoid conflicts)
    from models import User
    existing = await db_session.get(User, mock_db_user.id)
    if not existing:
        # Check by telegram_id just in case
        res = await db_session.execute(select(User).where(User.telegram_id == mock_db_user.telegram_id))
        if not res.scalars().first():
             db_session.add(mock_db_user)
             await db_session.commit()
    
    # Simulate text trigger
    update_message.message.text = "📱 My Subscriptions"
    
    from bot_handler import my_subscriptions
    await my_subscriptions(update_message, context)
    
    # Check response (assuming no subs)
    update_message.message.reply_text.assert_called()
    args = update_message.message.reply_text.call_args
    text = args[0][0] if args[0] else args.kwargs.get('text', '')
    assert "no active" in text.lower()

@pytest.mark.asyncio
async def test_tutorials_menu_handler(update_message, context, session_patch, db_session, mock_db_user):
    # User setup (idempotent)
    existing = await db_session.execute(select(User).where(User.telegram_id == mock_db_user.telegram_id))
    if not existing.scalars().first():
         db_session.add(mock_db_user)
         await db_session.commit()

    update_message.message.text = "📚 Tutorials"
    
    from user_features import tutorials_menu
    await tutorials_menu(update_message, context)
    
    update_message.message.reply_text.assert_called()
    # Check inline keyboard
    call_args = update_message.message.reply_text.call_args
    keyboard = call_args.kwargs['reply_markup'].inline_keyboard
    
    # Flatten
    keys = [col.callback_data for row in keyboard for col in row]
    assert 'tutorial_android' in keys
    assert 'tutorial_ios' in keys
    assert 'download_apps' in keys

@pytest.mark.asyncio
async def test_buy_service_entry(update_message, context, session_patch, db_session, mock_db_user):
    existing = await db_session.execute(select(User).where(User.telegram_id == mock_db_user.telegram_id))
    if not existing.scalars().first():
         db_session.add(mock_db_user)
         await db_session.commit()
    
    update_message.message.text = "🛒 Buy Service"
    
    from bot_handler import buy_service
    state = await buy_service(update_message, context)
    
    assert state is not None
    update_message.message.reply_text.assert_called()

@pytest.mark.asyncio
async def test_wallet_menu_entry_msg(update_message, context, session_patch, db_session, mock_db_user):
    existing = await db_session.execute(select(User).where(User.telegram_id == mock_db_user.telegram_id))
    if not existing.scalars().first():
         db_session.add(mock_db_user)
         await db_session.commit()
    
    update_message.message.text = "💰 Wallet"
    
    from bot_handler import wallet_menu
    state = await wallet_menu(update_message, context)
    
    update_message.message.reply_text.assert_called()
    call_args = update_message.message.reply_text.call_args
    msg_text = call_args[0][0]
    assert "Balance" in msg_text
    
    keyboard = call_args.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    has_topup = any(k.startswith('topup_') for k in keys)
    assert has_topup

@pytest.mark.asyncio
async def test_purchase_history_handler(update_message, context, session_patch, db_session, mock_db_user):
    existing = await db_session.execute(select(User).where(User.telegram_id == mock_db_user.telegram_id))
    if not existing.scalars().first():
         db_session.add(mock_db_user)
         await db_session.commit()
    
    update_message.message.text = "📜 History"
    
    from user_features import purchase_history
    await purchase_history(update_message, context)
    
    update_message.message.reply_text.assert_called()
    args = update_message.message.reply_text.call_args
    assert "history" in args[0][0].lower() or "transaction" in args[0][0].lower()

@pytest.mark.asyncio
async def test_support_menu_entry(update_message, context, session_patch, db_session, mock_db_user):
    existing = await db_session.execute(select(User).where(User.telegram_id == mock_db_user.telegram_id))
    if not existing.scalars().first():
         db_session.add(mock_db_user)
         await db_session.commit()
    
    update_message.message.text = "💬 Support"
    
    from support_tickets import support_menu
    await support_menu(update_message, context)
    
    update_message.message.reply_text.assert_called()
    call_args = update_message.message.reply_text.call_args
    
    keyboard = call_args.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    assert 'ticket_create' in keys
    assert 'ticket_list' in keys

@pytest.mark.asyncio
async def test_menu_structure_completeness(update_message, context, session_patch):
    from bot_handler import start
    await start(update_message, context)
    
    args = update_message.message.reply_text.call_args
    keyboard = args.kwargs['reply_markup'].keyboard
    
    def get_text(btn):
        return btn.text if isinstance(btn, KeyboardButton) else btn

    # Verify rows
    row1 = [get_text(btn) for btn in keyboard[0]]
    row2 = [get_text(btn) for btn in keyboard[1]]
    row3 = [get_text(btn) for btn in keyboard[2]]

    # Check for presence of strings
    assert any("My Subscriptions" in t for t in row1)
    assert any("Tutorials" in t for t in row1)
    assert any("Buy Service" in t for t in row2)
    assert any("History" in t for t in row2)
    assert any("Support" in t for t in row3)
    assert any("Wallet" in t for t in row3)

