"""Unit tests for shared WireGuard delivery helpers."""

from types import SimpleNamespace

from vpn_bot.utils import LanguageManager, ensure_telegram_text
from vpn_bot.wg_delivery import _wg_config_caption, build_wg_delivery_payload


def _fake_sub():
    interface = SimpleNamespace(
        public_key="a" * 43 + "=",
        endpoint_host="10.0.0.1",
        listen_port=51820,
        dns="1.1.1.1",
        mtu=1420,
        keepalive=25,
        server_id=1,
    )
    profile = SimpleNamespace(volume_gb=10)
    return SimpleNamespace(
        peer_private_key="b" * 43 + "=",
        assigned_ip="10.0.0.5",
        unique_identifier="WG-ABC",
        interface=interface,
        profile=profile,
    )


def test_build_wg_delivery_payload_has_required_keys():
    LanguageManager.load_locales()
    sub = _fake_sub()
    server = SimpleNamespace(host="81.30.108.27")
    payload = build_wg_delivery_payload(sub, server)
    assert payload is not None
    assert "[Interface]" in payload["config_text"]
    assert payload["unique_id"] == "WG-ABC"
    assert payload["wg_url"].startswith("wg://")
    assert payload["filename"] == "WG-ABC.conf"
    assert payload["volume_gb"] == 10


def test_build_wg_delivery_payload_returns_none_without_interface():
    sub = _fake_sub()
    sub.interface = None
    assert build_wg_delivery_payload(sub, SimpleNamespace(host="x")) is None


def test_ensure_telegram_text_never_empty():
    LanguageManager.load_locales()
    assert ensure_telegram_text("").strip()
    assert ensure_telegram_text(None).strip()


def test_wg_config_caption_not_empty():
    LanguageManager.load_locales()
    LanguageManager._current_lang = "fa"
    cap = _wg_config_caption("WG-1", "1404/01/01", 5, "wg://1.2.3.4:51820?x=1#WG-1")
    assert cap.strip()
