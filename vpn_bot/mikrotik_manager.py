import os
import routeros_api
import threading
import time
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
from vpn_bot.config import config
from vpn_bot.utils import logger
import socket
from vpn_bot.mt_cache import mt_cache, _CACHE_MISS, get_cached_manager

# Global socket timeout for RouterOS API (override via MIKROTIK_SOCKET_TIMEOUT)
socket.setdefaulttimeout(float(os.getenv("MIKROTIK_SOCKET_TIMEOUT", "10")))

_AUTH_ERROR_TOKENS = ("password", "invalid user", "denied", "login", "authentication", "failure for user")
_TRANSIENT_ERROR_TOKENS = (
    "timeout",
    "timed out",
    "connection",
    "reset",
    "broken pipe",
    "interrupted",
    "eof",
    "network",
    "unreachable",
    "refused",
    "temporarily",
)


def is_mikrotik_auth_error(exc: BaseException) -> bool:
    err = str(exc).lower()
    return any(tok in err for tok in _AUTH_ERROR_TOKENS)


def is_mikrotik_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    err = str(exc).lower()
    return any(tok in err for tok in _TRANSIENT_ERROR_TOKENS)

# Global connection pool to store routeros_api.RouterOsApiPool instances by (host, port, username)
_CONNECTION_POOL: Dict[str, routeros_api.RouterOsApiPool] = {}

class MikroTikManager:
    """
    Manager for MikroTik RouterOS interactions.
    Handles User Manager v7, PPP, WireGuard, and more.
    Uses connection pooling to minimize handshake delays.
    """
    
    def __init__(self, host: str = None, username: str = None, password: str = None, port: int = 8728, use_pool: bool = True):
        self.host = host or config.MIKROTIK_HOST
        self.username = username or config.MIKROTIK_USERNAME
        self.password = password or config.MIKROTIK_PASSWORD
        self.port = port
        self.use_pool = use_pool
        self.connection = None
        self.api = None
        self._api_lock = threading.Lock()

    def _run_locked(self, fn: Callable[[], Any]) -> Any:
        """Serialize RouterOS API access (pool + to_thread is not thread-safe)."""
        with self._api_lock:
            return fn()

    @staticmethod
    def drop_connection_pool(host: str, port: int, username: str) -> None:
        """Drop pooled API connection after timeout (stale socket may block next call)."""
        pool_key = f"{host}:{port}:{username}"
        conn = _CONNECTION_POOL.pop(pool_key, None)
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
            logger.info("Dropped MikroTik pool for %s after slow/failed WG sync", host)

    def _probe_connection(self) -> None:
        """Lightweight liveness check (read system identity, not full tree)."""
        try:
            identity = self.api.get_resource("/system/identity")
            identity.get()
        except Exception as exc:
            logger.debug(
                "Identity probe failed for %s, falling back to /system/resource: %s",
                self.host,
                exc,
            )
            resource = self.api.get_resource("/system/resource")
            resource.get()

    def connect(self):
        """Establish connection to MikroTik router using pooling if enabled."""
        pool_key = f"{self.host}:{self.port}:{self.username}"

        try:
            if self.use_pool and pool_key in _CONNECTION_POOL:
                self.connection = _CONNECTION_POOL[pool_key]
                try:
                    self.api = self.connection.get_api()
                    self._probe_connection()
                    logger.debug(f"Reusing existing MikroTik connection for {self.host}")
                    return
                except Exception as e:
                    logger.warning(f"Cached connection for {self.host} failed, reconnecting... ({e})")
                    _CONNECTION_POOL.pop(pool_key, None)

            from vpn_bot.config import config

            use_ssl = self.port in [443, 8729]
            self.connection = routeros_api.RouterOsApiPool(
                self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                plaintext_login=True,
                use_ssl=use_ssl,
                ssl_verify=config.MIKROTIK_SSL_VERIFY if use_ssl else False,
            )
            self.api = self.connection.get_api()

            if self.use_pool:
                _CONNECTION_POOL[pool_key] = self.connection

            logger.info(f"New MikroTik connection established for {self.host} (SSL: {use_ssl})")
        except Exception as e:
            if is_mikrotik_auth_error(e):
                logger.error(
                    "MikroTik login failed for %s@%s:%s — check server credentials in admin panel "
                    "and RouterOS user permissions (API).",
                    self.username,
                    self.host,
                    self.port,
                )
            else:
                logger.error(f"Failed to connect to MikroTik at {self.host}: {e}")
            raise

    def connect_with_retry(self, max_attempts: int | None = None, base_delay: float | None = None) -> None:
        """Connect with exponential backoff; no retry on auth failures."""
        attempts = max_attempts if max_attempts is not None else int(
            os.getenv("MIKROTIK_CONNECT_RETRIES", "3")
        )
        delay = base_delay if base_delay is not None else float(
            os.getenv("MIKROTIK_RETRY_BACKOFF_SEC", "2")
        )
        last_exc: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                if attempt > 1:
                    MikroTikManager.drop_connection_pool(self.host, self.port, self.username)
                    time.sleep(delay * (2 ** (attempt - 2)))
                self.connect()
                return
            except Exception as exc:
                last_exc = exc
                if is_mikrotik_auth_error(exc):
                    MikroTikManager.drop_connection_pool(self.host, self.port, self.username)
                    raise
                logger.warning(
                    "MikroTik connect attempt %d/%d failed for %s: %s",
                    attempt,
                    attempts,
                    self.host,
                    exc,
                )
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("MikroTik connect failed without exception")

    def close(self):
        """
        Close connection. 
        If pooling is enabled, we DON'T disconnect the underlying pool, just reset local state.
        """
        if self.connection and not self.use_pool:
            try:
                self.connection.disconnect()
            except:
                pass
        
        self.connection = None
        self.api = None

    def _get_resource(self, path: str):
        """Helper to get a resource API object with auto-reconnect logic."""
        error_count = 0
        while error_count < 2:
            try:
                if not self.api:
                    self.connect()
                return self.api.get_resource(path)
            except Exception as e:
                error_count += 1
                logger.warning(f"API Resource access failed (attempt {error_count}): {e}")
                self.api = None # Force reconnect
                if error_count >= 2:
                    raise
        return None

    def create_user(self, username: str, password: str, profile_name: str) -> bool:
        """Create a user in User Manager v7 and assign a profile."""
        user_created = False
        try:
            user_api = self._get_resource('/user-manager/user')
            user_prof_api = self._get_resource('/user-manager/user-profile')

            # 1. Create User
            user_api.add(
                name=username,
                password=password,
                disabled='no'
            )
            user_created = True

            # 2. Assign Profile
            user_prof_api.add(
                user=username,
                profile=profile_name
            )

            logger.info(f"Created User Manager user {username} with profile {profile_name}")
            mt_cache.invalidate(f"{self.host}:all_um_users")
            return True
        except Exception as e:
            logger.error(f"create_user error: {e}")
            if user_created:
                try:
                    self.delete_user(username)
                except Exception:
                    pass
            return False

    def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch user usage and status from User Manager v7. (Cached: 15s TTL)"""
        _ck = f"{self.host}:user_info:{username}"
        _cv = mt_cache.get(_ck)
        if _cv is not _CACHE_MISS:
            return _cv
        try:
            users = self._get_resource('/user-manager/user').get(name=username)
            if not users: return None
            user = users[0]
            
            # Fetch Sessions for connectivity
            sessions = self._get_resource('/user-manager/session').get(user=username, active='true')
            is_connected = len(sessions) > 0
            
            # Parse stats (v7 stats are usually in bytes)
            download_used = int(user.get('download-used', 0))
            upload_used = int(user.get('upload-used', 0))
            used_bytes = download_used + upload_used
            
            status = 'active'
            if user.get('disabled') == 'true':
                status = 'banned'
            
            result = {
                'username': username,
                'password': user.get('password'),
                'used_bytes': used_bytes,
                'status': status,
                'is_active': user.get('disabled') != 'true',
                'connected_devices': len(sessions),
                'current_ip': sessions[0].get('address') if is_connected else None
            }
            mt_cache.set(_ck, result, 15)
            return result
        except Exception as e:
            logger.error(f"get_user_info error: {e}")
            return None

    def add_data_to_user(self, username: str, additional_gb: int) -> bool:
        """Increase transfer limit for the user's profile limitation (lim_{profile})."""
        try:
            up_api = self._get_resource('/user-manager/user-profile')
            lim_api = self._get_resource('/user-manager/limitation')

            ups = up_api.get(user=username)
            if not ups:
                logger.warning(f"add_data_to_user: no user-profile for {username}")
                return False

            profile_name = ups[0].get('profile')
            if not profile_name:
                return False

            lim_name = f"lim_{profile_name}"
            lims = lim_api.get(name=lim_name)
            if not lims:
                lims = lim_api.get(name=username)

            if not lims:
                logger.warning(f"add_data_to_user: limitation {lim_name} not found")
                return False

            add_bytes = additional_gb * 1024**3
            lim = lims[0]
            current = int(lim.get('transfer-limit') or lim.get('total-limit') or 0)
            new_limit = current + add_bytes
            lim_api.set(id=lim['id'], **{'transfer-limit': str(new_limit)})
            mt_cache.invalidate(f"{self.host}:user_info:{username}")
            logger.info(f"Added {additional_gb}GB to {username} via {lim_name}")
            return True
        except Exception as e:
            logger.error(f"add_data_to_user error: {e}")
            return False

    def set_user_data_limit(self, username: str, total_gb: int) -> bool:
        """Set absolute transfer-limit (bytes) for the user's profile limitation."""
        try:
            up_api = self._get_resource('/user-manager/user-profile')
            lim_api = self._get_resource('/user-manager/limitation')

            ups = up_api.get(user=username)
            if not ups:
                logger.warning(f"set_user_data_limit: no user-profile for {username}")
                return False

            profile_name = ups[0].get('profile')
            if not profile_name:
                return False

            lim_name = f"lim_{profile_name}"
            lims = lim_api.get(name=lim_name)
            if not lims:
                lims = lim_api.get(name=username)
            if not lims:
                logger.warning(f"set_user_data_limit: limitation {lim_name} not found")
                return False

            total_bytes = int(total_gb) * 1024**3
            lim_api.set(id=lims[0]['id'], **{'transfer-limit': str(total_bytes)})
            mt_cache.invalidate(f"{self.host}:user_info:{username}")
            logger.info(f"Set {total_gb}GB cap for {username} via {lim_name}")
            return True
        except Exception as e:
            logger.error(f"set_user_data_limit error: {e}")
            return False

    def disable_user(self, username: str) -> bool:
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                user_api.set(id=users[0]['id'], disabled='true')
                self.disconnect_user_session(username)
            mt_cache.invalidate(f"{self.host}:user_info:{username}")
            return True
        except Exception as e:
            logger.error(f"disable_user error: {e}")
            return False

    def enable_user(self, username: str) -> bool:
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                user_api.set(id=users[0]['id'], disabled='false')
            mt_cache.invalidate(f"{self.host}:user_info:{username}")
            return True
        except Exception as e:
            logger.error(f"enable_user error: {e}")
            return False

    def reset_password(self, username: str, new_pass: str) -> bool:
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                user_api.set(id=users[0]['id'], password=new_pass)
                return True
            return False
        except Exception as e:
            logger.error(f"reset_password error: {e}")
            return False

    def set_user_expiry(self, username: str, expiry_date: "datetime") -> bool:
        """Set the User Manager user's expiry to an absolute datetime.

        This keeps DB and router expiry in sync when stacking renewal days.
        RouterOS v7 UM accepts the expire field in ISO-like format
        ``YYYY-MM-DD HH:MM:SS``.
        """
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if not users:
                return False
            expire_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")
            user_api.set(id=users[0]['id'], expire=expire_str)
            return True
        except Exception as e:
            logger.error(f"set_user_expiry error for {username}: {e}")
            return False

    def extend_validity(self, username: str, additional_days: int) -> bool:
        """Extend a User Manager user's validity by ``additional_days`` from now.

        Prefer ``set_user_expiry`` (which stacks from the current DB expiry)
        when the new absolute expiry is already known in the DB layer.
        """
        from datetime import datetime as _datetime, timezone
        try:
            now = _datetime.now(timezone.utc)
            new_expiry = now + timedelta(days=additional_days)
            return self.set_user_expiry(username, new_expiry)
        except Exception as e:
            logger.error(f"extend_validity error: {e}")
            return False

    def delete_user(self, username: str) -> bool:
        try:
            self.disconnect_user_session(username)
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                user_api.remove(id=users[0]['id'])
            
            # Also cleanup user-profile and unique limitations
            up_api = self._get_resource('/user-manager/user-profile')
            ups = up_api.get(user=username)
            for up in ups: up_api.remove(id=up['id'])
            
            mt_cache.invalidate(f"{self.host}:user_info:{username}", f"{self.host}:all_um_users")
            return True
        except Exception as e:
            logger.error(f"delete_user error: {e}")
            return False

    def disconnect_user_session(self, username: str) -> bool:
        try:
            session_api = self._get_resource('/user-manager/session')
            sessions = session_api.get(user=username, active='true')
            for s in sessions:
                session_api.remove(id=s['id'])
            return True
        except Exception as e:
            logger.error(f"disconnect_user_session error: {e}")
            return False

    def create_profile_with_limits(self, name: str, validity_days: int, data_limit_gb: int, rate_limit: str = None) -> bool:
        """Helper to create v7 Profile & Limitation with optional rate limit.
        Handles cases where they already exist by updating them.
        Uses reconnection between steps for stability on slow UM v7 APIs.
        """
        try:
            lim_name = f"lim_{name}"
            total_bytes = data_limit_gb * 1024**3
            lim_params = {'transfer-limit': str(total_bytes)}
            if rate_limit:
                if '/' in rate_limit:
                    upload, download = rate_limit.split('/')
                    lim_params['rate-limit-rx'] = upload
                    lim_params['rate-limit-tx'] = download
                else:
                    lim_params['rate-limit-rx'] = rate_limit
                    lim_params['rate-limit-tx'] = rate_limit
            
            # Step 1: Handle Limitation
            try:
                self.connect()
                lim_api = self.api.get_resource('/user-manager/limitation')
                existing_lims = lim_api.get(name=lim_name)
                if existing_lims:
                    lim_api.set(id=existing_lims[0]['id'], **lim_params)
                    logger.info(f"Updated existing limitation: {lim_name}")
                else:
                    lim_api.add(name=lim_name, **lim_params)
                    logger.info(f"Created new limitation: {lim_name}")
            finally:
                self.close()

            # Step 2: Handle Profile
            try:
                self.connect()
                prof_api = self.api.get_resource('/user-manager/profile')
                prof_params = {'validity': f"{validity_days}d"}
                existing_profs = prof_api.get(name=name)
                if existing_profs:
                    prof_api.set(id=existing_profs[0]['id'], **prof_params)
                    logger.info(f"Updated existing profile: {name}")
                else:
                    prof_api.add(name=name, **prof_params)
                    logger.info(f"Created new profile: {name}")
            finally:
                self.close()

            # Step 3: Handle Linking
            try:
                self.connect()
                pl_api = self.api.get_resource('/user-manager/profile-limitation')
                existing_links = pl_api.get(profile=name, limitation=lim_name)
                if not existing_links:
                    pl_api.add(profile=name, limitation=lim_name)
                    logger.info(f"Linked profile {name} to limitation {lim_name}")
            finally:
                self.close()
            
            return True
        except Exception as e:
            logger.error(f"create_profile_with_limits error: {e}")
            return False

    # --- WireGuard Methods ---

    def create_wg_interface(self, name: str, listen_port: int, address: str = "10.0.0.1/24") -> Optional[Dict[str, str]]:
        """Create a WireGuard interface and assign an IP address. Returns keys and actual listen_port. Retries on port collision."""
        try:
            wg_api = self._get_resource('/interface/wireguard')
            addr_api = self._get_resource('/ip/address')
            
            # Check if exists
            exists = wg_api.get(name=name)
            current_port = listen_port
            max_retries = 10
            
            if not exists:
                for attempt in range(max_retries):
                    try:
                        wg_api.add(name=name, listen_port=str(current_port))
                        logger.info(f"Created WireGuard interface {name} on port {current_port}")
                        break
                    except Exception as e:
                        str(e).lower()
                        # If the error is likely due to port collision or generic failure to add, retry
                        logger.warning(f"Failed to create WG {name} on port {current_port} (attempt {attempt+1}): {e}. Trying next port.")
                        current_port += 1
                else:
                    logger.error(f"Failed to create WG interface {name} after {max_retries} attempts.")
                    return None
            
            # Get Interface Info (to get public-key)
            info = wg_api.get(name=name)[0]
            
            # Extract the actual port used in case it was an existing interface
            actual_port = int(info.get('listen-port', current_port))
            
            # Add IP address if not set
            ip_exists = addr_api.get(interface=name)
            if not ip_exists:
                addr_api.add(address=address, interface=name)
                logger.info(f"Assigned IP {address} to WireGuard interface {name}")
            
            return {
                'name': name,
                'public_key': info.get('public-key'),
                'private_key': info.get('private-key', 'managed-by-router'),
                'listen_port': actual_port
            }
        except Exception as e:
            logger.error(f"create_wg_interface error: {e}")
            return None

    def update_wg_interface_port(self, name: str, port: int) -> bool:
        """Update the listen port of an existing WireGuard interface."""
        try:
            self.connect()
            wg_api = self.api.get_resource('/interface/wireguard')
            existing = wg_api.get(name=name)
            if existing:
                wg_api.set(id=existing[0]['id'], **{'listen-port': str(port)})
                logger.info(f"Updated WireGuard interface {name} port to {port}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update WG interface port: {e}")
            return False
        finally:
            self.close()

    def update_wg_interface_address(self, name: str, address: str) -> bool:
        """Update the IP address of an existing WireGuard interface."""
        try:
            self.connect()
            addr_api = self.api.get_resource('/ip/address')
            existing = addr_api.get(interface=name)
            if existing:
                addr_api.set(id=existing[0]['id'], address=address)
                logger.info(f"Updated WireGuard interface {name} address to {address}")
                return True
            else:
                # If no address exists, add it
                addr_api.add(address=address, interface=name)
                logger.info(f"Added IP address {address} to interface {name}")
                return True
        except Exception as e:
            logger.error(f"update_wg_interface_address error: {e}")
            return False
        finally:
            self.close()

    def sync_wg_interface_automation(
        self,
        name: str,
        address: str,
        upstream: str = None,
        routing_mark: str = None,
        nat_routing_mark: str = None,
        nat_dst: str = None,
        nat_dst_list: str = None,
        nat_dst_negate: bool = True,
        gateway: str = None,
        route_table: str = None,
        route_dst: str = None,
        route_distance: int = None,
        listen_port: int = None,
    ) -> tuple[bool, bool]:
        """Automate NAT/Mangle/Filter/Route. Returns (success, route_applied_on_router)."""
        return self._run_locked(
            lambda: self._sync_wg_interface_automation_impl(
                name,
                address,
                upstream,
                routing_mark,
                nat_routing_mark,
                nat_dst,
                nat_dst_list,
                nat_dst_negate,
                gateway,
                route_table,
                route_dst,
                route_distance,
                listen_port,
            )
        )

    def _sync_wg_interface_automation_impl(
        self,
        name: str,
        address: str,
        upstream: str = None,
        routing_mark: str = None,
        nat_routing_mark: str = None,
        nat_dst: str = None,
        nat_dst_list: str = None,
        nat_dst_negate: bool = True,
        gateway: str = None,
        route_table: str = None,
        route_dst: str = None,
        route_distance: int = None,
        listen_port: int = None,
    ) -> tuple[bool, bool]:
        t0 = time.monotonic()
        route_applied = False
        try:
            self.connect_with_retry()
            nat_api = self.api.get_resource('/ip/firewall/nat')
            mangle_api = self.api.get_resource('/ip/firewall/mangle')
            route_api = self.api.get_resource('/ip/route')
            filter_api = self.api.get_resource('/ip/firewall/filter')

            import ipaddress
            try:
                subnet = str(ipaddress.ip_interface(address).network)
            except Exception:
                subnet = address

            def _upsert_rule(api, comment: str, params: dict) -> None:
                rows = api.get(comment=comment)
                if rows:
                    api.set(id=rows[0]['id'], **params)
                else:
                    api.add(**params)

            if listen_port:
                filter_comment = f"managed-by-bot-wg-filter-{name}"
                _upsert_rule(
                    filter_api,
                    filter_comment,
                    {
                        'chain': 'input',
                        'action': 'accept',
                        'protocol': 'udp',
                        'dst-port': str(listen_port),
                        'comment': filter_comment,
                    },
                )

            nat_comment = f"managed-by-bot-wg-nat-{name}"
            nat_params = {
                'chain': 'srcnat',
                'action': 'masquerade',
                'src-address': subnet,
                'comment': nat_comment,
            }
            if nat_dst_list:
                list_val = nat_dst_list
                if nat_dst_negate and not list_val.startswith("!"):
                    list_val = f"!{list_val}"
                nat_params["dst-address-list"] = list_val
            elif nat_dst:
                if nat_dst_negate:
                    nat_params["dst-address"] = f"!{nat_dst}"
                elif nat_dst != "127.0.0.1":
                    nat_params["dst-address"] = nat_dst
            if upstream:
                nat_params['out-interface'] = upstream
            if nat_routing_mark:
                nat_params['routing-mark'] = nat_routing_mark
            _upsert_rule(nat_api, nat_comment, nat_params)

            if routing_mark:
                rm_name = routing_mark
                try:
                    rt_api = self.api.get_resource('/routing/table')
                    if not rt_api.get(name=rm_name):
                        rt_api.add(name=rm_name, fib='yes')
                except Exception as rt_err:
                    logger.error(f"Routing table check/add failed: {rt_err}")

                mangle_comment = f"managed-by-bot-wg-mangle-{name}"
                _upsert_rule(
                    mangle_api,
                    mangle_comment,
                    {
                        'chain': 'prerouting',
                        'action': 'mark-routing',
                        'src-address': subnet,
                        'new-routing-mark': routing_mark,
                        'passthrough': 'no',
                        'comment': mangle_comment,
                    },
                )

            effective_table = route_table or routing_mark
            if gateway and effective_table:
                try:
                    rt_api = self.api.get_resource('/routing/table')
                    if not rt_api.get(name=effective_table):
                        rt_api.add(name=effective_table, fib='yes')
                except Exception as rt_err:
                    logger.error(f"Route table check/add failed: {rt_err}")

                route_comment = f"managed-by-bot-wg-route-{name}"
                route_params = {
                    'dst-address': route_dst or '0.0.0.0/0',
                    'gateway': gateway,
                    'routing-table': effective_table,
                    'distance': str(route_distance if route_distance is not None else 1),
                    'comment': route_comment,
                }
                _upsert_rule(route_api, route_comment, route_params)
                route_applied = True
            elif gateway or route_table or (route_dst and route_dst != '0.0.0.0/0'):
                logger.info(
                    "WG route skipped for %s on %s:%s — gateway=%r effective_table=%r "
                    "(need both gateway and routing-table/routing-mark)",
                    name,
                    self.host,
                    self.port,
                    gateway,
                    effective_table,
                )

            elapsed = time.monotonic() - t0
            logger.info(
                "WG firewall sync for %s on %s:%s finished in %.1fs route_applied=%s",
                name,
                self.host,
                self.port,
                elapsed,
                route_applied,
            )
            return True, route_applied
        except Exception as e:
            if is_mikrotik_transient_error(e):
                MikroTikManager.drop_connection_pool(self.host, self.port, self.username)
            logger.error(
                "sync_wg_interface_automation error for %s on %s:%s: %s",
                name,
                self.host,
                self.port,
                e,
            )
            return False, False
        finally:
            self.close()

    def _cleanup_wg_automation_on_connected_api(self, name: str) -> None:
        """Remove NAT/Mangle/Route/Filter rules (requires active self.api)."""
        addr_list_api = self.api.get_resource('/ip/firewall/address-list')
        nat_api = self.api.get_resource('/ip/firewall/nat')
        mangle_api = self.api.get_resource('/ip/firewall/mangle')
        route_api = self.api.get_resource('/ip/route')
        filter_api = self.api.get_resource('/ip/firewall/filter')

        for item in addr_list_api.get(list=f"WG_LIST_{name}"):
            addr_list_api.remove(id=item['id'])

        for item in nat_api.get(comment=f"managed-by-bot-wg-nat-{name}"):
            nat_api.remove(id=item['id'])

        for item in mangle_api.get(comment=f"managed-by-bot-wg-mangle-{name}"):
            mangle_api.remove(id=item['id'])

        for item in route_api.get(comment=f"managed-by-bot-wg-route-{name}"):
            route_api.remove(id=item['id'])

        for item in filter_api.get(comment=f"managed-by-bot-wg-filter-{name}"):
            filter_api.remove(id=item['id'])

    def _delete_wg_interface_on_connected_api(self, name: str) -> bool:
        """Remove WG interface, peers, and addresses (requires active self.api)."""
        peer_api = self.api.get_resource('/interface/wireguard/peers')
        addr_api = self.api.get_resource('/ip/address')
        wg_api = self.api.get_resource('/interface/wireguard')

        peers = peer_api.get(interface=name)
        for p in peers:
            try:
                peer_api.remove(id=p['id'])
            except Exception as e:
                logger.warning(f"Failed to remove peer {p.get('comment', '?')}: {e}")
        logger.info(f"Removed {len(peers)} peers from {name}")

        addrs = addr_api.get(interface=name)
        for a in addrs:
            try:
                addr_api.remove(id=a['id'])
            except Exception as e:
                logger.warning(f"Failed to remove address {a.get('address', '?')}: {e}")

        ifaces = wg_api.get(name=name)
        if ifaces:
            wg_api.remove(id=ifaces[0]['id'])
            logger.info(f"Deleted WireGuard interface {name}")
        return True

    def cleanup_wg_interface_automation(self, name: str) -> bool:
        """Remove all automation rules for a WG interface."""
        def _work():
            try:
                self.connect()
                self._cleanup_wg_automation_on_connected_api(name)
                return True
            except Exception as e:
                logger.error(f"cleanup_wg_interface_automation error: {e}")
                return False
            finally:
                self.close()

        return self._run_locked(_work)

    def remove_wg_interface_complete(self, name: str) -> bool:
        """Single RouterOS session: automation cleanup + interface delete."""
        def _work():
            try:
                self.connect()
                self._cleanup_wg_automation_on_connected_api(name)
                self._delete_wg_interface_on_connected_api(name)
                mt_cache.invalidate(f"{self.host}:")
                return True
            except Exception as e:
                logger.error(f"remove_wg_interface_complete error for {name}: {e}")
                return False
            finally:
                self.close()

        return self._run_locked(_work)

    def reset_wg_peer_for_renewal(
        self,
        interface: str,
        public_key: str,
        allowed_address: str,
        comment: str,
    ) -> bool:
        """Remove and re-add a WireGuard peer to reset cumulative RX/TX counters.

        RouterOS peer byte counters are cumulative; deleting and re-adding the
        same public key gives the user a fresh quota baseline on renewal.
        """
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peers = peer_api.get(interface=interface, public_key=public_key)
            if peers:
                peer_api.remove(id=peers[0]['id'])
            peer_api.add(
                interface=interface,
                public_key=public_key,
                allowed_address=allowed_address,
                comment=comment,
                disabled='no',
            )
            mt_cache.invalidate(f"{self.host}:all_wg_peers:{interface}")
            logger.info(
                "Reset WG peer traffic for renewal on %s (key=%s…)",
                interface,
                public_key[:8] if public_key else "?",
            )
            return True
        except Exception as e:
            logger.error(f"reset_wg_peer_for_renewal error: {e}")
            return False

    def add_wg_peer(self, interface: str, public_key: str, allowed_address: str, comment: str) -> bool:
        """Add a Peer to a WireGuard interface."""
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peer_api.add(
                interface=interface,
                public_key=public_key,
                allowed_address=allowed_address,
                comment=comment
            )
            logger.info(f"Added WireGuard peer to {interface} with allowed-address {allowed_address}")
            mt_cache.invalidate(f"{self.host}:all_wg_peers:{interface}")
            return True
        except Exception as e:
            logger.error(f"add_wg_peer error: {e}")
            return False

    def add_wg_queue(self, name: str, target_ip: str, rate_limit: str) -> bool:
        """Create a Simple Queue for a WireGuard peer."""
        try:
            queue_api = self._get_resource('/queue/simple')
            # Check if exists
            exists = queue_api.get(name=name)
            if exists:
                queue_api.set(id=exists[0]['id'], target=target_ip, max_limit=rate_limit)
                logger.info(f"Updated Simple Queue {name} for IP {target_ip} with limit {rate_limit}")
            else:
                queue_api.add(name=name, target=target_ip, max_limit=rate_limit)
                logger.info(f"Created Simple Queue {name} for IP {target_ip} with limit {rate_limit}")
            return True
        except Exception as e:
            logger.error(f"add_wg_queue error: {e}")
            return False

    def remove_wg_queue(self, name: str) -> bool:
        """Remove a Simple Queue by name."""
        try:
            queue_api = self._get_resource('/queue/simple')
            exists = queue_api.get(name=name)
            if exists:
                queue_api.remove(id=exists[0]['id'])
                logger.info(f"Removed Simple Queue {name}")
                return True
            return False
        except Exception as e:
            logger.error(f"remove_wg_queue error: {e}")
            return False

    def remove_wg_peer(self, interface: str, public_key: str) -> bool:
        """Remove a WireGuard peer by public key."""
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peers = peer_api.get(interface=interface, public_key=public_key)
            if peers:
                peer_api.remove(id=peers[0]['id'])
                logger.info(f"Removed WireGuard peer {public_key} from {interface}")
                mt_cache.invalidate(f"{self.host}:all_wg_peers:{interface}")
                return True
            return False
        except Exception as e:
            logger.error(f"remove_wg_peer error: {e}")
            return False

    def remove_wg_peer_by_comment(self, comment: str) -> bool:
        """Remove WireGuard peer(s) by comment."""
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peers = peer_api.get(comment=comment)
            if peers:
                for p in peers:
                    peer_api.remove(id=p['id'])
                logger.info(f"Removed WireGuard peer(s) with comment: {comment}")
                return True
            return False
        except Exception as e:
            logger.error(f"remove_wg_peer_by_comment error: {e}")
            return False

    def get_wg_peer_stats(self, interface: str, public_key: str) -> Optional[Dict[str, int]]:
        """Get RX/TX bytes for a specific peer."""
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peers = peer_api.get(interface=interface, public_key=public_key)
            if peers:
                peer = peers[0]
                return {
                    'rx': int(peer.get('rx', 0)),
                    'tx': int(peer.get('tx', 0)),
                    'last_handshake': peer.get('last-handshake')
                }
            return None
        except Exception as e:
            logger.error(f"get_wg_peer_stats error: {e}")
            return None

    def get_wg_peer(self, interface: str, public_key: str) -> Optional[Dict[str, Any]]:
        """Get full details for a specific peer."""
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peers = peer_api.get(interface=interface, public_key=public_key)
            if peers:
                return peers[0]
            return None
        except Exception as e:
            logger.error(f"get_wg_peer error: {e}")
            return None

    def get_wg_peer_info(self, interface: str, public_key: str) -> Optional[Dict[str, Any]]:
        """Alias for get_wg_peer (used by admin WG service and live E2E tests)."""
        return self.get_wg_peer(interface, public_key)

    def set_wg_peer_status(self, interface: str, public_key: str, disabled: bool) -> bool:
        """Enable or disable a WireGuard peer."""
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            peers = peer_api.get(interface=interface, public_key=public_key)
            if peers:
                peer_api.set(id=peers[0]['id'], disabled='yes' if disabled else 'no')
                mt_cache.invalidate(f"{self.host}:all_wg_peers:{interface}")
                return True
            return False
        except Exception as e:
            logger.error(f"set_wg_peer_status error: {e}")
            return False

    def get_all_wg_peers(self, interface: str) -> List[Dict[str, Any]]:
        """Fetch all peers for a specific interface. (Cached: 30s TTL)"""
        _ck = f"{self.host}:all_wg_peers:{interface}"
        _cv = mt_cache.get(_ck)
        if _cv is not _CACHE_MISS:
            return _cv
        try:
            peer_api = self._get_resource('/interface/wireguard/peers')
            result = peer_api.get(interface=interface)
            mt_cache.set(_ck, result, 30)
            return result
        except Exception as e:
            logger.error(f"get_all_wg_peers error: {e}")
            return None

    def get_all_um_users(self) -> List[Dict[str, Any]]:
        """Fetch all User Manager users. (Cached: 60s TTL)"""
        _ck = f"{self.host}:all_um_users"
        _cv = mt_cache.get(_ck)
        if _cv is not _CACHE_MISS:
            return _cv
        try:
            user_api = self._get_resource('/user-manager/user')
            result = user_api.get()
            mt_cache.set(_ck, result, 60)
            return result
        except Exception as e:
            logger.error(f"get_all_um_users error: {e}")
            return None

    def get_all_queues(self) -> List[Dict[str, Any]]:
        """Fetch all simple queues. (Cached: 60s TTL)"""
        _ck = f"{self.host}:all_queues"
        _cv = mt_cache.get(_ck)
        if _cv is not _CACHE_MISS:
            return _cv
        try:
            queue_api = self._get_resource('/queue/simple')
            result = queue_api.get()
            mt_cache.set(_ck, result, 60)
            return result
        except Exception as e:
            logger.error(f"get_all_queues error: {e}")
            return None

    # --- Safety & Upstream Management ---

    def find_available_wg_interface_params(self, base_name="bot_wg", base_subnet="10.") -> Optional[Dict[str, Any]]:
        """
        Find next available interface name and subnet.
        Smart logic: Reclaims indices if rules exist but interface is gone.
        """
        try:
            self.connect()
            
            # 1. Get existing WG interfaces
            wg_api = self.api.get_resource('/interface/wireguard')
            existing_wg = wg_api.get()
            existing_names = {x['name'] for x in existing_wg}
            
            existing_ports = set()
            for x in existing_wg:
                port_str = x.get('listen-port')
                if port_str and str(port_str).isdigit():
                    existing_ports.add(int(port_str))

            # 2. Get existing IP subnets and their associated interfaces
            addr_api = self.api.get_resource('/ip/address')
            existing_addrs = addr_api.get()
            
            # Map subnet index -> interface name
            subnet_to_iface = {}
            for addr in existing_addrs:
                # address format: 10.5.0.1/24
                address_val = addr.get('address', '')
                if address_val.startswith(base_subnet):
                    parts = address_val.split('/') [0].split('.')
                    if len(parts) >= 2:
                        idx = int(parts[1])
                        subnet_to_iface[idx] = addr.get('interface', '')

            # 3. Find first free or reclaimable index
            idx = 1
            while idx < 255:
                potential_name = f"{base_name}{idx}"
                legacy_name = f"wg{idx}"
                
                # Check if interface name exists
                if potential_name in existing_names or legacy_name in existing_names:
                    idx += 1
                    continue
                
                # Check if subnet is used by another ACTIVE interface
                if idx in subnet_to_iface:
                    owner_iface = subnet_to_iface[idx]
                    # If the owner interface doesn't exist in our current interface list, it's an orphan!
                    if owner_iface and owner_iface in existing_names:
                        # Truly occupied by someone else
                        idx += 1
                        continue
                    # Else: It's orphaned or owner_iface is invalid, we can reclaim it!
                
                # Determine port, avoiding known occupied WG ports
                target_port = 51820 + (idx - 1)
                while target_port in existing_ports:
                    target_port += 1
                
                # Found free or reclaimable slot
                return {
                    'name': potential_name,
                    'address': f"{base_subnet}{idx}.0.1/24",
                    'subnet_idx': idx,
                    'listen_port': target_port
                }
            
            return None
        except Exception as e:
            logger.error(f"find_available_wg_interface_params error: {e}")
            return None
        finally:
            self.close()

    def set_wg_upstream_interface(self, interface_name: str) -> bool:
        """Update or create NAT Masquerade rule for WireGuard subnet to use specific interface."""
        try:
            self.connect()
            nat_api = self.api.get_resource('/ip/firewall/nat')
            
            # Find rule for 10.0.0.0/8 (covers all WG subnets)
            # We look for src-address=10.0.0.0/8 AND action=masquerade
            # We filter by finding them manually as API filtering on complex fields can be tricky
            all_nat = nat_api.get(chain='srcnat', action='masquerade')
            
            target_rule_id = None
            for rule in all_nat:
                src = rule.get('src-address', '')
                # Check for 10.0.0.0/8 or similar wide ranges or if it has our comment
                if src == '10.0.0.0/8' or rule.get('comment') == 'managed-by-bot-wg-nat':
                    target_rule_id = rule['id']
                    break
            
            if target_rule_id:
                nat_api.set(id=target_rule_id, **{'out-interface': interface_name, 'comment': 'managed-by-bot-wg-nat'})
                logger.info(f"Updated WG NAT rule to use out-interface: {interface_name}")
            else:
                nat_api.add(
                    chain='srcnat',
                    action='masquerade',
                    src_address='10.0.0.0/8',
                    out_interface=interface_name,
                    comment='managed-by-bot-wg-nat'
                )
                logger.info(f"Created WG NAT rule using out-interface: {interface_name}")
                
            return True
        except Exception as e:
            logger.error(f"set_wg_upstream_interface error: {e}")
            return False
        finally:
            self.close()

    def migrate_wg_peers(self, old_interface: str, new_interface: str, 
                         peers: list, new_subnet_prefix: str = None,
                         remove_from_old: bool = True) -> dict:
        """
        Migrate WireGuard peers from old_interface to new_interface on MikroTik.
        
        Args:
            old_interface: Name of the source interface (e.g. 'wg1')
            new_interface: Name of the destination interface (e.g. 'bot_wg2')
            peers: List of dicts with keys: 'public_key', 'allowed_address', 'comment'
            new_subnet_prefix: If provided (e.g. '10.2.0.'), remap IPs to this subnet
            remove_from_old: Whether to remove peers from old interface after adding to new
            
        Returns:
            dict with 'added': int, 'removed': int, 'failed': int, 'new_ips': dict
        """
        result = {'added': 0, 'removed': 0, 'failed': 0, 'new_ips': {}}
        
        try:
            self.connect()
            peer_api = self._get_resource('/interface/wireguard/peers')
            
            for peer in peers:
                try:
                    # Determine the allowed-address for the new interface
                    old_addr = peer['allowed_address']  # e.g. '10.1.0.5/32'
                    new_addr = old_addr
                    
                    if new_subnet_prefix:
                        # Extract host part from old IP (e.g. '5' from '10.1.0.5/32')
                        ip_part = old_addr.split('/')[0]  # '10.1.0.5'
                        host_part = ip_part.split('.')[-1]  # '5'
                        new_addr = f"{new_subnet_prefix}{host_part}/32"
                        result['new_ips'][old_addr.split('/')[0]] = new_addr.split('/')[0]
                    
                    # Add peer to new interface
                    peer_api.add(
                        interface=new_interface,
                        public_key=peer['public_key'],
                        allowed_address=new_addr,
                        comment=peer.get('comment', '')
                    )
                    result['added'] += 1
                    logger.info(f"Migrated peer {peer.get('comment', '?')} to {new_interface} ({new_addr})")
                    
                    # Remove from old interface if requested
                    if remove_from_old:
                        try:
                            old_peers = peer_api.get(interface=old_interface, public_key=peer['public_key'])
                            if old_peers:
                                peer_api.remove(id=old_peers[0]['id'])
                                result['removed'] += 1
                        except Exception:
                            pass  # Old interface may not exist anymore
                            
                except Exception as e:
                    logger.error(f"Failed to migrate peer {peer.get('comment', '?')}: {e}")
                    result['failed'] += 1
            
            return result
        except Exception as e:
            logger.error(f"migrate_wg_peers error: {e}")
            return result
        finally:
            self.close()

    def delete_wg_interface(self, name: str) -> bool:
        """Delete a WireGuard interface and all its peers/addresses from MikroTik."""
        def _work():
            try:
                self.connect()
                self._delete_wg_interface_on_connected_api(name)
                mt_cache.invalidate(f"{self.host}:")
                return True
            except Exception as e:
                logger.error(f"delete_wg_interface error: {e}")
                return False
            finally:
                self.close()

        return self._run_locked(_work)

    def get_wg_interfaces(self) -> List[Dict[str, Any]]:
        """Get list of existing WireGuard interfaces. (Cached: 120s TTL)"""
        _ck = f"{self.host}:wg_interfaces"
        _cv = mt_cache.get(_ck)
        if _cv is not _CACHE_MISS:
            return _cv
        try:
            self.connect()
            api = self.api.get_resource('/interface/wireguard')
            result = api.get()
            mt_cache.set(_ck, result, 120)
            return result
        except Exception as e:
            logger.error(f"get_wg_interfaces error: {e}")
            return []
        finally:
            self.close()

    def get_wg_interface_address_map(self) -> dict[str, str]:
        """Map WireGuard interface name -> primary /ip/address CIDR."""
        try:
            self.connect()
            addr_api = self.api.get_resource('/ip/address')
            mapping: dict[str, str] = {}
            for row in addr_api.get() or []:
                iface = row.get('interface') or ''
                addr = row.get('address') or ''
                if iface and addr and iface not in mapping:
                    mapping[iface] = addr
            return mapping
        except Exception as e:
            logger.error(f"get_wg_interface_address_map error: {e}")
            return {}
        finally:
            self.close()

    def get_routing_tables(self) -> Optional[List[str]]:
        """Get list of existing routing tables (Routing Marks). Cached 120s. None on error."""
        cache_key = f"{self.host}:{self.port}:routing_tables"
        cached = mt_cache.get(cache_key)
        if cached is not _CACHE_MISS:
            return cached

        def _fetch():
            try:
                self.connect()
                api = self.api.get_resource('/routing/table')
                tables = api.get()
                return [t['name'] for t in tables]
            except Exception as e:
                logger.error(f"get_routing_tables error: {e}")
                return None
            finally:
                self.close()

        result = self._run_locked(_fetch)
        if result is None:
            return None
        mt_cache.set(cache_key, result, 120)
        return result

    _UPSTREAM_WAN_TYPES = frozenset({
        "ether", "vlan", "bridge", "bonding", "gre-tunnel", "ipip-tunnel",
        "eoip-tunnel", "pppoe-out", "pptp-out", "l2tp-out", "sstp-out",
    })

    def _is_upstream_candidate(self, iface: dict) -> bool:
        name = (iface.get("name") or "").lower()
        if name.startswith("bot_wg") or name.startswith("wg_list_"):
            return False
        if (iface.get("type") or "") == "wg":
            return False
        if iface.get("disabled") != "false":
            return False
        if iface.get("type") == "loopback":
            return False
        itype = iface.get("type") or ""
        if iface.get("dynamic") == "true":
            return itype in self._UPSTREAM_WAN_TYPES or itype.endswith("-out")
        return True

    def get_firewall_address_list_names(self) -> Optional[List[str]]:
        """Unique /ip firewall address-list names on router. Cached 120s."""
        cache_key = f"{self.host}:{self.port}:fw_addr_list_names"
        cached = mt_cache.get(cache_key)
        if cached is not _CACHE_MISS:
            return cached

        def _fetch():
            try:
                self.connect()
                api = self.api.get_resource("/ip/firewall/address-list")
                rows = api.get() or []
                names = sorted({r.get("list") for r in rows if r.get("list")})
                return names
            except Exception as e:
                logger.error(f"get_firewall_address_list_names error: {e}")
                return None
            finally:
                self.close()

        result = self._run_locked(_fetch)
        if result is None:
            return None
        mt_cache.set(cache_key, result, 120)
        return result

    def get_upstream_interfaces(self) -> Optional[List[Dict[str, Any]]]:
        """Potential NAT out-interfaces (WAN; includes dynamic PPPoE/SSTP out). Cached 120s."""
        cache_key = f"{self.host}:{self.port}:upstream_ifaces"
        cached = mt_cache.get(cache_key)
        if cached is not _CACHE_MISS:
            return cached

        def _fetch():
            try:
                self.connect()
                api = self.api.get_resource('/interface')
                ints = api.get()
                valid = []
                for i in ints:
                    if not self._is_upstream_candidate(i):
                        continue
                    valid.append({
                        'name': i['name'],
                        'running': i.get('running') == 'true',
                        'type': i.get('type') or '',
                    })
                valid.sort(key=lambda x: (x['running'] is not True, x['name']))
                return valid
            except Exception as e:
                logger.error(f"get_upstream_interfaces error: {e}")
                return None
            finally:
                self.close()

        result = self._run_locked(_fetch)
        if result is None:
            return None
        mt_cache.set(cache_key, result, 120)
        return result

    def set_user_shared_users(self, username: str, count: int) -> bool:
        """Set `shared-users` on a User Manager user (per-user override)."""
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                user_api.set(id=users[0]['id'], **{'shared-users': str(count)})
                logger.info(f"Set shared-users={count} for UM user {username}")
                mt_cache.invalidate(f"{self.host}:shared_users:{username}")
                return True
            return False
        except Exception as e:
            logger.error(f"set_user_shared_users error: {e}")
            return False

    def get_user_shared_users(self, username: str) -> Optional[int]:
        """Get current `shared-users` value for a User Manager user. (Cached: 30s TTL)"""
        _ck = f"{self.host}:shared_users:{username}"
        _cv = mt_cache.get(_ck)
        if _cv is not _CACHE_MISS:
            return _cv
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                val = users[0].get('shared-users', '1')
                result = int(val) if val else 1
                mt_cache.set(_ck, result, 30)
                return result
            return None
        except Exception as e:
            logger.error(f"get_user_shared_users error: {e}")
            return None

def get_mikrotik_manager(server=None):
    """Return a singleton MikroTikManager instance for the given server (Phase 1)."""
    return get_cached_manager(server)


def get_mikrotik_list_reader(server=None) -> "MikroTikManager":
    """
    Manager for read-only list fetches; reuses the shared connection pool.
    Execution is serialized per-server via run_mikrotik_for_server (mt_session).
    """
    from vpn_bot.config import config

    if server:
        return MikroTikManager(
            host=server.host,
            username=server.username,
            password=server.password,
            port=server.port,
            use_pool=True,
        )
    return MikroTikManager(
        host=config.MIKROTIK_HOST,
        username=config.MIKROTIK_USERNAME,
        password=config.MIKROTIK_PASSWORD,
        port=config.MIKROTIK_PORT,
        use_pool=True,
    )

