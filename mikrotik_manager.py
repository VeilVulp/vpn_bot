import routeros_api
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta
from config import config
from utils import logger

class MikroTikManager:
    """
    Manager for MikroTik RouterOS interactions.
    Handles PPP secrets, profiles, active connections, and queues.
    """
    
    def __init__(self, host: str = None, username: str = None, password: str = None, port: int = 8728):
        self.host = host or config.MIKROTIK_HOST
        self.username = username or config.MIKROTIK_USERNAME
        self.password = password or config.MIKROTIK_PASSWORD
        self.port = port
        self.connection = None
        self.api = None

    def connect(self):
        """Establish connection to MikroTik router."""
        try:
            self.connection = routeros_api.RouterOsApiPool(
                self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                plaintext_login=True
            )
            self.api = self.connection.get_api()
            logger.info(f"Connected to MikroTik at {self.host}")
        except Exception as e:
            logger.error(f"Failed to connect to MikroTik: {e}")
            raise

    def close(self):
        """Close connection."""
        if self.connection:
            self.connection.disconnect()
            logger.info("Disconnected from MikroTik")

    def _get_resource(self, path: str):
        """Helper to get a resource API object."""
        if not self.api:
            self.connect()
        return self.api.get_resource(path)

    def create_user(self, username: str, password: str, profile_name: str) -> bool:
        """Create a user in User Manager v7 and assign a profile."""
        try:
            user_api = self._get_resource('/user-manager/user')
            user_prof_api = self._get_resource('/user-manager/user-profile')
            
            # 1. Create User
            user_api.add(
                name=username,
                password=password,
                attributes={"status": "active"}
            )
            
            # 2. Assign Profile
            user_prof_api.add(
                user=username,
                profile=profile_name
            )
            
            logger.info(f"Created User Manager user {username} with profile {profile_name}")
            return True
        except Exception as e:
            logger.error(f"create_user error: {e}")
            return False

    def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch user usage and status from User Manager v7."""
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
            
            # Total bytes comes from the profile/limitation (harder to fetch directly from user, 
            # usually we store it in DB, but let's try to get it if possible)
            # For simplicity, we assume we want to return what ROS knows.
            # Limitations are in '/user-manager/limitation'
            
            status = 'active'
            if user.get('disabled') == 'true':
                status = 'banned'
            
            # Note: User Manager v7 manages expiration automatically if profile is set.
            # We report what we found.
            
            return {
                'username': username,
                'password': user.get('password'),
                'used_bytes': used_bytes,
                'status': status,
                'is_active': user.get('disabled') != 'true',
                'connected_devices': len(sessions),
                'current_ip': sessions[0].get('address') if is_connected else None
            }
        except Exception as e:
            logger.error(f"get_user_info error: {e}")
            return None

    def add_data_to_user(self, username: str, additional_gb: int) -> bool:
        """In User Manager v7, usually involves adding a new user-profile or extending limitation."""
        # For this bot's simple logic, we might need a unique Limitation per user 
        # or just reset stats. ROS v7 stats are cumulative.
        # Adding data usually means increasing the "allowed" limit.
        try:
            # This is complex in UM if using shared profiles.
            # If we assume unique per-user limitation named after username:
            lim_api = self._get_resource('/user-manager/limitation')
            lims = lim_api.get(name=username)
            if lims:
                current_limit = int(lims[0].get('total-limit', 0))
                new_limit = current_limit + (additional_gb * 1024**3)
                lim_api.set(id=lims[0]['id'], **{'total-limit': str(new_limit)})
                return True
            return False
        except Exception as e:
            logger.error(f"add_data_to_user error: {e}")
            return False

    def disable_user(self, username: str) -> bool:
        try:
            user_api = self._get_resource('/user-manager/user')
            users = user_api.get(name=username)
            if users:
                user_api.set(id=users[0]['id'], disabled='true')
                self.disconnect_user_session(username)
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

    def extend_validity(self, username: str, additional_days: int) -> bool:
        """In v7 User Manager, we usually add a new user-profile or extend the current one."""
        try:
            up_api = self._get_resource('/user-manager/user-profile')
            ups = up_api.get(user=username)
            if ups:
                 # ROS v7 UM profiles are complex. Usually we add a new one 
                 # or re-assign to trigger validity reset.
                 # For simplicity, we assume we just re-assign the same profile.
                 p_name = ups[0]['profile']
                 up_api.add(user=username, profile=p_name)
                 return True
            return False
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
        """Helper to create v7 Profile & Limitation with optional rate limit."""
        try:
            lim_api = self._get_resource('/user-manager/limitation')
            prof_api = self._get_resource('/user-manager/profile')
            pl_api = self._get_resource('/user-manager/profile-limitation')
            
            # 1. Create Limitation
            lim_name = f"lim_{name}"
            total_bytes = data_limit_gb * 1024**3
            lim_params = {'total-limit': str(total_bytes)}
            if rate_limit:
                lim_params['rate-limit'] = rate_limit
                
            lim_api.add(
                name=lim_name,
                **lim_params
            )
            
            # 2. Create Profile
            prof_api.add(
                name=name,
                **{'validity': f"{validity_days}d"}
            )
            
            # 3. Link them
            pl_api.add(
                profile=name,
                limitation=lim_name
            )
            return True
        except Exception as e:
            logger.error(f"create_profile_with_limits error: {e}")
            return False

