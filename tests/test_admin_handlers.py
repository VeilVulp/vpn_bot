
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User as TagUser, Message, CallbackQuery, Chat, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler

# Modules to test
from admin_panel import admin_start, connection_status_menu
from admin_settings import bot_config_menu, manage_connection_info, manage_payment_cards

@pytest.fixture
def update_callback_admin(mock_telegram_user):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock(spec=TagUser)
    update.effective_user.id = mock_telegram_user["id"]
    update.effective_chat = MagicMock(spec=Chat)
    update.effective_chat.id = mock_telegram_user["id"]
    
    update.callback_query = MagicMock(spec=CallbackQuery)
    update.callback_query.id = "query_id"
    update.callback_query.data = "some_data"
    update.callback_query.answer = AsyncMock()
    # Mock message on callback_query for edit_text
    update.callback_query.message = MagicMock(spec=Message)
    update.callback_query.message.edit_text = AsyncMock()
    
    # Also mock edit_message_text on callback_query itself as fallback (some handlers use it)
    update.callback_query.edit_message_text = AsyncMock()
    
    update.message = MagicMock(spec=Message)
    update.message.reply_text = AsyncMock() # Fallback
    return update

@pytest.fixture
def context():
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.user_data = {}
    return ctx

@pytest.fixture
def admin_patch(mock_telegram_user):
    with patch('config.config.ADMIN_IDS', [mock_telegram_user['id']]):
        yield

@pytest.fixture(autouse=True)
def mock_db_dep(db_session):
    # Patch DB session for both modules
    class SessionContextManager:
        def __init__(self):
            self.session = db_session
        async def __aenter__(self):
            return self.session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_factory = MagicMock(return_value=SessionContextManager())
    
    with patch('admin_panel.AsyncSessionLocal', mock_factory), \
         patch('admin_settings.AsyncSessionLocal', mock_factory):
        yield

@pytest.mark.asyncio
async def test_admin_start_menu(update_callback_admin, context, admin_patch):
    await admin_start(update_callback_admin, context)
    
    # Check if we edited the message or sent a new one
    if update_callback_admin.callback_query:
        update_callback_admin.callback_query.message.edit_text.assert_called()
        call = update_callback_admin.callback_query.message.edit_text.call_args
    else:
        update_callback_admin.message.reply_text.assert_called()
        call = update_callback_admin.message.reply_text.call_args
        
    keyboard = call.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    
    assert 'connection_status_menu' in keys
    assert 'bot_config_menu' in keys
    assert 'settings_menu' not in keys # Old one should be gone

@pytest.mark.asyncio
async def test_connection_status_menu(update_callback_admin, context, admin_patch):
    await connection_status_menu(update_callback_admin, context)
    
    update_callback_admin.callback_query.edit_message_text.assert_called()
    call = update_callback_admin.callback_query.edit_message_text.call_args
    keyboard = call.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    
    # Sub-buttons check
    assert 'manage_ovpn' in keys
    assert 'settings_connection' in keys # L2TP/SSTP Credentials
    assert 'admin_start' in keys # Back button

@pytest.mark.asyncio
async def test_bot_config_menu(update_callback_admin, context, admin_patch):
    await bot_config_menu(update_callback_admin, context)
    
    update_callback_admin.callback_query.edit_message_text.assert_called()
    call = update_callback_admin.callback_query.edit_message_text.call_args
    keyboard = call.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    
    assert 'settings_cards' in keys
    assert 'settings_messages' in keys
    assert 'settings_presets' in keys
    assert 'settings_subjects' in keys
    assert 'admin_start' in keys # Back button

@pytest.mark.asyncio
async def test_manage_connection_info_back_link(update_callback_admin, context, admin_patch, db_session, mock_server):
    # Seed server
    async with db_session as session:
        session.add(mock_server)
        await session.commit()
        await session.refresh(mock_server)

    await manage_connection_info(update_callback_admin, context)
    
    update_callback_admin.callback_query.edit_message_text.assert_called()
    call = update_callback_admin.callback_query.edit_message_text.call_args
    keyboard = call.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    
    # Now valid servers are present, so we expect conn_server_X
    assert any('conn_server_' in k for k in keys)
    assert 'connection_status_menu' in keys # Back link

@pytest.mark.asyncio
async def test_manage_payment_cards_back_link(update_callback_admin, context, admin_patch):
    # This tests that sub-menus of Bot Configs go back to 'bot_config_menu'
    await manage_payment_cards(update_callback_admin, context)
    
    update_callback_admin.callback_query.edit_message_text.assert_called()
    call = update_callback_admin.callback_query.edit_message_text.call_args
    keyboard = call.kwargs['reply_markup'].inline_keyboard
    keys = [col.callback_data for row in keyboard for col in row]
    
    assert 'bot_config_menu' in keys

