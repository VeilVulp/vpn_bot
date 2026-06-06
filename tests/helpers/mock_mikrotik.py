"""In-memory MikroTik stub for DB/load tests."""

from __future__ import annotations

import asyncio
import random
import string
from typing import Any


class MockMikrotikManager:
    """Simulates User Manager + WireGuard API with optional latency and failures."""

    _instances: dict[int, "MockMikrotikManager"] = {}

    def __init__(self, server_id: int = 0, latency_ms: tuple[int, int] = (5, 30), fail_rate: float = 0.0):
        self.server_id = server_id
        self.latency_ms = latency_ms
        self.fail_rate = fail_rate
        self.um_users: dict[str, dict[str, Any]] = {}
        self.um_limitations: dict[str, dict[str, Any]] = {}
        self.um_user_profiles: dict[str, str] = {}
        self.wg_peers: dict[str, dict[str, dict[str, Any]]] = {}
        self.wg_queues: dict[str, dict[str, Any]] = {}
        self.disabled_users: set[str] = set()
        self.create_user_calls: list[str] = []
        self.disable_user_calls: list[str] = []
        self.enable_user_calls: list[str] = []
        self.delete_user_calls: list[str] = []
        self.add_wg_peer_calls: list[tuple[str, str]] = []
        self.reset_wg_peer_for_renewal_calls: list[tuple[str, str, str, str]] = []
        self.set_wg_peer_status_calls: list[tuple[str, str, bool]] = []
        self.add_wg_queue_calls: list[tuple[str, str, str]] = []
        self.automation_calls: list[dict[str, Any]] = []
        self.cleanup_calls: list[str] = []
        MockMikrotikManager._instances[server_id] = self

    async def _delay(self):
        lo, hi = self.latency_ms
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)

    def _maybe_fail(self) -> bool:
        return random.random() < self.fail_rate

    def connect(self):
        return True

    def close(self):
        pass

    def create_user(self, username: str, password: str, profile: str) -> bool:
        if self._maybe_fail():
            return False
        self.um_users[username] = {
            "name": username,
            "password": password,
            "profile": profile,
            "disabled": "false",
            "download-used": "0",
            "upload-used": "0",
        }
        self.um_user_profiles[username] = profile
        lim_name = f"lim_{profile}"
        if lim_name not in self.um_limitations:
            self.um_limitations[lim_name] = {
                "name": lim_name,
                "transfer-limit": str(1024**3),
            }
        self.create_user_calls.append(username)
        return True

    def delete_user(self, username: str) -> bool:
        self.um_users.pop(username, None)
        self.um_user_profiles.pop(username, None)
        self.disabled_users.discard(username)
        self.delete_user_calls.append(username)
        return True

    def disable_user(self, username: str) -> bool:
        self.disabled_users.add(username)
        if username in self.um_users:
            self.um_users[username]["disabled"] = "true"
        self.disable_user_calls.append(username)
        return True

    def enable_user(self, username: str) -> bool:
        self.disabled_users.discard(username)
        if username in self.um_users:
            self.um_users[username]["disabled"] = "false"
        self.enable_user_calls.append(username)
        return True

    def get_user_info(self, username: str) -> dict | None:
        u = self.um_users.get(username)
        if not u:
            return None
        disabled = u.get("disabled") == "true" or username in self.disabled_users
        dl = int(u.get("download-used", 0))
        ul = int(u.get("upload-used", 0))
        return {
            "username": username,
            "used_bytes": dl + ul,
            "status": "banned" if disabled else "active",
            "is_active": not disabled,
            "connected_devices": 0,
            "current_ip": None,
        }

    def get_all_um_users(self) -> list[dict] | None:
        out = []
        for name, u in self.um_users.items():
            row = dict(u)
            if name in self.disabled_users:
                row["disabled"] = "true"
            out.append(row)
        return out

    def extend_validity(self, username: str, days: int) -> bool:
        return username in self.um_users

    def reset_password(self, username: str, new_password: str) -> bool:
        if username in self.um_users:
            self.um_users[username]["password"] = new_password
            return True
        return False

    def create_profile_with_limits(
        self, name: str, validity_days: int, data_limit_gb: int, rate_limit: str | None = None
    ) -> bool:
        lim_name = f"lim_{name}"
        lim: dict[str, Any] = {
            "name": lim_name,
            "transfer-limit": str(data_limit_gb * 1024**3),
        }
        if rate_limit and "/" in rate_limit:
            upload, download = rate_limit.split("/", 1)
            lim["rate-limit-rx"] = upload
            lim["rate-limit-tx"] = download
        self.um_limitations[lim_name] = lim
        return True

    def add_data_to_user(self, username: str, gb: int) -> bool:
        profile = self.um_user_profiles.get(username)
        if not profile:
            return False
        lim_name = f"lim_{profile}"
        lim = self.um_limitations.get(lim_name)
        if not lim:
            return False
        current = int(lim.get("transfer-limit", 0))
        lim["transfer-limit"] = str(current + gb * 1024**3)
        return True

    def set_user_data_limit(self, username: str, total_gb: int) -> bool:
        profile = self.um_user_profiles.get(username)
        if not profile:
            return False
        lim_name = f"lim_{profile}"
        lim = self.um_limitations.setdefault(
            lim_name, {"name": lim_name, "transfer-limit": "0"}
        )
        lim["transfer-limit"] = str(int(total_gb) * 1024**3)
        return True

    def get_all_wg_peers(self, interface_name: str) -> list[dict] | None:
        iface = self.wg_peers.get(interface_name, {})
        peers = []
        for pub, data in iface.items():
            peers.append(
                {
                    "public-key": pub,
                    "disabled": data.get("disabled", "false"),
                    "rx": str(data.get("rx", 0)),
                    "tx": str(data.get("tx", 0)),
                }
            )
        return peers

    def set_wg_peer_status(self, interface_name: str, public_key: str, disabled: bool = True) -> bool:
        iface = self.wg_peers.setdefault(interface_name, {})
        if public_key in iface:
            iface[public_key]["disabled"] = "true" if disabled else "false"
            self.set_wg_peer_status_calls.append((interface_name, public_key, disabled))
            return True
        return False

    def get_wg_peer_stats(self, interface: str, public_key: str) -> dict | None:
        iface = self.wg_peers.get(interface, {})
        peer = iface.get(public_key)
        if not peer:
            return None
        return {
            "rx": int(peer.get("rx", 0)),
            "tx": int(peer.get("tx", 0)),
        }

    def remove_wg_peer(self, interface_name: str, public_key: str) -> bool:
        iface = self.wg_peers.get(interface_name, {})
        if public_key in iface:
            del iface[public_key]
            return True
        return False

    def remove_wg_queue(self, name: str) -> bool:
        if name in self.wg_queues:
            del self.wg_queues[name]
            return True
        return False

    def add_wg_peer(self, interface_name: str, public_key: str, allowed_address: str, comment: str) -> bool:
        if self._maybe_fail():
            return False
        self.wg_peers.setdefault(interface_name, {})[public_key] = {
            "public-key": public_key,
            "disabled": "false",
            "comment": comment,
            "rx": 0,
            "tx": 0,
        }
        self.add_wg_peer_calls.append((interface_name, public_key))
        return True

    def reset_wg_peer_for_renewal(
        self,
        interface: str,
        public_key: str,
        allowed_address: str,
        comment: str,
    ) -> bool:
        if self._maybe_fail():
            return False
        peers = self.wg_peers.setdefault(interface, {})
        peers.pop(public_key, None)
        peers[public_key] = {
            "public-key": public_key,
            "disabled": "false",
            "comment": comment,
            "rx": 0,
            "tx": 0,
        }
        self.reset_wg_peer_for_renewal_calls.append(
            (interface, public_key, allowed_address, comment)
        )
        return True

    def add_wg_queue(self, name: str, target: str, rate_limit: str) -> bool:
        self.wg_queues[name] = {"name": name, "target": target, "max-limit": rate_limit}
        self.add_wg_queue_calls.append((name, target, rate_limit))
        return True

    def get_all_queues(self) -> list[dict] | None:
        return list(self.wg_queues.values())

    def find_available_wg_interface_params(self) -> dict | None:
        suffix = "".join(random.choices(string.digits, k=3))
        return {
            "name": f"wg_test_{suffix}",
            "listen_port": 51820 + random.randint(1, 100),
            "address": f"10.99.{random.randint(1, 200)}.1/24",
        }

    def create_wg_interface(self, name: str, listen_port: int, address: str) -> dict | None:
        return {
            "public_key": "mock_pub_" + name,
            "private_key": "mock_priv_" + name,
            "listen_port": listen_port,
        }

    def sync_wg_interface_automation(
        self,
        name: str,
        address: str,
        upstream: str | None = None,
        routing_mark: str | None = None,
        nat_routing_mark: str | None = None,
        nat_dst: str | None = None,
        nat_dst_list: str | None = None,
        nat_dst_negate: bool = True,
        gateway: str | None = None,
        route_table: str | None = None,
        route_dst: str | None = None,
        route_distance: int | None = None,
        listen_port: int | None = None,
        **kwargs: Any,
    ) -> tuple[bool, bool]:
        effective_table = route_table or routing_mark
        route_applied = bool(gateway and effective_table)
        self.automation_calls.append(
            {
                "name": name,
                "address": address,
                "upstream": upstream,
                "routing_mark": routing_mark,
                "nat_routing_mark": nat_routing_mark,
                "nat_dst": nat_dst,
                "nat_dst_list": nat_dst_list,
                "nat_dst_negate": nat_dst_negate,
                "gateway": gateway,
                "route_table": route_table,
                "route_dst": route_dst,
                "route_distance": route_distance,
                "listen_port": listen_port,
                "route_applied": route_applied,
            }
        )
        return True, route_applied

    def cleanup_wg_interface_automation(self, name: str) -> bool:
        self.cleanup_calls.append(name)
        return True

    def last_automation(self) -> dict[str, Any] | None:
        return self.automation_calls[-1] if self.automation_calls else None

    def automations_for(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.automation_calls if c.get("name") == name]

    def get_upstream_interfaces(self) -> list[dict[str, Any]]:
        return [
            {"name": "ether1", "running": "true", "type": "ether"},
            {"name": "SSTP-USA", "running": "true", "type": "sstp-out"},
        ]

    def get_firewall_address_list_names(self) -> list[str]:
        return ["RFC1918", "IR"]

    def get_routing_tables(self) -> list[str]:
        return ["main", "wg_mark_test"]

    def delete_wg_interface(self, name: str) -> bool:
        self.wg_peers.pop(name, None)
        return True

    def set_peer_traffic(self, interface: str, public_key: str, rx: int, tx: int) -> None:
        """Test helper: set cumulative router counters for a peer."""
        iface = self.wg_peers.setdefault(interface, {})
        if public_key not in iface:
            iface[public_key] = {"public-key": public_key, "disabled": "false", "rx": 0, "tx": 0}
        iface[public_key]["rx"] = rx
        iface[public_key]["tx"] = tx

    def set_um_usage(self, username: str, download: int, upload: int) -> None:
        if username in self.um_users:
            self.um_users[username]["download-used"] = str(download)
            self.um_users[username]["upload-used"] = str(upload)

    @classmethod
    def for_server(cls, server_id: int) -> "MockMikrotikManager":
        if server_id not in cls._instances:
            cls._instances[server_id] = cls(server_id=server_id)
        return cls._instances[server_id]

    @classmethod
    def reset_all(cls):
        cls._instances.clear()


def patch_get_mikrotik_manager(monkeypatch):
    """Patch factory so each Server.id gets a stable mock instance."""

    def _factory(server=None):
        sid = server.id if server is not None else 0
        return MockMikrotikManager.for_server(sid)

    monkeypatch.setattr("vpn_bot.mikrotik_manager.get_mikrotik_manager", _factory)
    monkeypatch.setattr("vpn_bot.user_features.get_mikrotik_manager", _factory, raising=False)
    monkeypatch.setattr("vpn_bot.sync_manager.get_mikrotik_manager", _factory)
    monkeypatch.setattr("vpn_bot.admin_subscription_service.get_mikrotik_manager", _factory)
    monkeypatch.setattr("vpn_bot.admin_cleanup.get_mikrotik_manager", _factory)
    monkeypatch.setattr("vpn_bot.admin_profile_service.get_mikrotik_manager", _factory)
    monkeypatch.setattr("vpn_bot.admin_wg_service.get_mikrotik_manager", _factory, raising=False)
