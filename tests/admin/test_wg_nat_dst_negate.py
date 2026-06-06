"""NAT Dst. Address display, parsing, and MikroTik negate (!) flag."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vpn_bot.admin_panel import (
    _wg_format_nat_dst_display,
    _wg_parse_nat_dst_text,
)
from vpn_bot.mikrotik_manager import MikroTikManager


def test_format_nat_dst_with_negate_ip():
    iface = MagicMock(nat_dst_address="10.0.0.5", nat_dst_address_list=None, nat_dst_negate=True)
    assert _wg_format_nat_dst_display(iface) == "!10.0.0.5"


def test_format_nat_dst_without_negate_ip():
    iface = MagicMock(nat_dst_address="10.0.0.5", nat_dst_address_list=None, nat_dst_negate=False)
    assert _wg_format_nat_dst_display(iface) == "10.0.0.5"


def test_format_nat_dst_negate_list():
    iface = MagicMock(
        nat_dst_address="127.0.0.1",
        nat_dst_address_list="IR",
        nat_dst_negate=True,
    )
    assert _wg_format_nat_dst_display(iface) == "!list:IR"


def test_parse_nat_dst_text_bang_prefix():
    parsed = _wg_parse_nat_dst_text("!192.168.0.0/16", ["IR"])
    assert parsed == {"kind": "ip", "val": "192.168.0.0/16", "negate": True}


def test_parse_nat_dst_text_list_bang():
    parsed = _wg_parse_nat_dst_text("@blocked", [])
    assert parsed["kind"] == "list"
    assert parsed["val"] == "blocked"


@pytest.mark.parametrize(
    "negate,addr,list_name,expected_dst,expected_list",
    [
        (True, "127.0.0.1", None, "!127.0.0.1", None),
        (False, "192.168.1.1", None, "192.168.1.1", None),
        (True, "127.0.0.1", "wan_targets", None, "!wan_targets"),
    ],
)
def test_sync_nat_dst_negate_on_params(negate, addr, list_name, expected_dst, expected_list):
    captured = {}

    mgr = MikroTikManager(host="1.2.3.4", username="u", password="p", use_pool=False)
    mgr.connect = MagicMock()
    mgr.close = MagicMock()

    class FakeNat:
        def get(self, **kwargs):
            return []

        def set(self, **kwargs):
            captured.update(kwargs)

        def add(self, **kwargs):
            captured.update(kwargs)

    mgr.api = MagicMock()
    mgr.api.get_resource = MagicMock(
        side_effect=lambda path: {
            "/ip/firewall/nat": FakeNat(),
            "/ip/firewall/mangle": FakeNat(),
            "/ip/route": FakeNat(),
            "/ip/firewall/filter": FakeNat(),
            "/routing/table": FakeNat(),
        }.get(path, FakeNat())
    )

    mgr._sync_wg_interface_automation_impl(
        "wg_test",
        "10.0.0.1/24",
        nat_dst=addr,
        nat_dst_list=list_name,
        nat_dst_negate=negate,
    )

    if expected_list:
        assert captured.get("dst-address-list") == expected_list
    elif expected_dst:
        assert captured.get("dst-address") == expected_dst
