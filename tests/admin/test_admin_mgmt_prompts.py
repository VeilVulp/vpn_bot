"""Admin management flow locale keys."""

from vpn_bot.utils import LanguageManager


def test_admin_mgmt_add_prompt_resolves():
    text = LanguageManager.get("admin.admin_mgmt.add_prompt")
    assert "admin.admin_mgmt.add_prompt" not in text
    assert len(text) > 20


def test_admin_mgmt_remove_prompt_resolves():
    text = LanguageManager.get("admin.admin_mgmt.remove_prompt")
    assert "admin.admin_mgmt.remove_prompt" not in text
    assert len(text) > 20
