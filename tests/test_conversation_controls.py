"""Unit tests for shared conversation cancel/skip controls."""

from unittest.mock import MagicMock

from telegram import CallbackQuery, Chat, Message, Update, User

from vpn_bot.conversation_controls import (
    CANCEL_CALLBACK,
    SKIP_CALLBACK,
    append_conv_footer,
    conv_markup,
    is_conv_cancel,
    is_conv_skip,
    merge_markup,
)


def _message_update(text: str) -> Update:
    user = User(id=1, is_bot=False, first_name="T")
    chat = Chat(id=1, type="private")
    message = Message(message_id=1, date=None, chat=chat, text=text)
    message.set_bot(MagicMock())
    return Update(update_id=1, message=message)


def _callback_update(data: str) -> Update:
    user = User(id=1, is_bot=False, first_name="T")
    chat = Chat(id=1, type="private")
    message = Message(message_id=1, date=None, chat=chat)
    query = CallbackQuery(id="q", from_user=user, chat_instance="c", data=data, message=message)
    return Update(update_id=1, callback_query=query)


def test_conv_markup_has_cancel_and_skip():
    markup = conv_markup(with_skip=True)
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert len(labels) == 2
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert SKIP_CALLBACK in callbacks
    assert CANCEL_CALLBACK in callbacks


def test_is_conv_cancel_callback_and_command():
    assert is_conv_cancel(_callback_update(CANCEL_CALLBACK))
    assert is_conv_cancel(_message_update("/cancel"))
    assert not is_conv_cancel(_message_update("hello"))


def test_is_conv_skip_callback_and_command():
    assert is_conv_skip(_callback_update(SKIP_CALLBACK))
    assert is_conv_skip(_message_update("/skip"))
    assert not is_conv_skip(_message_update("hello"))


def test_append_conv_footer_once():
    base = "Prompt"
    once = append_conv_footer(base)
    twice = append_conv_footer(once)
    assert once != base
    assert twice == once


def test_merge_markup_appends_cancel_row():
    base = conv_markup()
    merged = merge_markup(base, with_cancel=True)
    assert len(merged.inline_keyboard) >= 2
