"""
MikroTik API Connection Tests

Tests for verifying connectivity and basic API operations with MikroTik router.
Based on: https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API
"""
import pytest
import time

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestMikroTikConnection:
    """Test basic API connectivity and authentication."""

    def test_connection_success(self, mikrotik_config):
        """Verify successful connection to MikroTik router."""
        from mikrotik_manager import MikroTikManager
        
        mgr = MikroTikManager(**mikrotik_config)
        try:
            mgr.connect()
            assert mgr.api is not None, "API object should be created"
            
            # Test basic command - get system identity
            resource = mgr._get_resource('/system/identity')
            result = resource.get()
            assert len(result) > 0, "Should get system identity"
            
        finally:
            mgr.close()

    def test_connection_get_system_resource(self, mikrotik_config):
        """Verify can fetch system resource information."""
        from mikrotik_manager import MikroTikManager
        
        mgr = MikroTikManager(**mikrotik_config)
        try:
            mgr.connect()
            
            resource = mgr._get_resource('/system/resource')
            result = resource.get()
            
            assert len(result) > 0
            assert 'version' in result[0], "Should have RouterOS version"
            assert 'uptime' in result[0], "Should have uptime"
            assert 'cpu-load' in result[0], "Should have CPU load"
            
        finally:
            mgr.close()

    def test_connection_wrong_credentials(self):
        """Verify proper error handling for wrong credentials."""
        from mikrotik_manager import MikroTikManager
        
        mgr = MikroTikManager(
            host="192.168.88.1",
            username="invalid_user",
            password="wrong_password"
        )
        
        with pytest.raises(Exception):
            mgr.connect()

    def test_user_manager_enabled(self, mikrotik_config):
        """Verify User Manager package is enabled."""
        from mikrotik_manager import MikroTikManager
        
        mgr = MikroTikManager(**mikrotik_config)
        try:
            mgr.connect()
            
            # Try to access User Manager
            um_users = mgr._get_resource('/user-manager/user')
            result = um_users.get()
            
            # If we get here without exception, UM is working
            assert isinstance(result, list), "Should return list of users"
            
        finally:
            mgr.close()


class TestMikroTikUserManagerCRUD:
    """Test User Manager CRUD operations."""

    @pytest.fixture
    def manager(self, mikrotik_config):
        """Provide a connected MikroTik manager."""
        from mikrotik_manager import MikroTikManager
        
        mgr = MikroTikManager(**mikrotik_config)
        mgr.connect()
        yield mgr
        mgr.close()

    @pytest.fixture
    def test_username(self):
        """Generate unique test username."""
        return f"pytest_user_{int(time.time())}"

    def test_create_user(self, manager, test_username):
        """Test creating a new User Manager user."""
        try:
            # Create user
            result = manager.create_user(
                username=test_username,
                password="TestPass123!",
                profile_name="default"  # Uses default profile
            )
            assert result is True, "User creation should succeed"
            
            # Verify user exists
            user_info = manager.get_user_info(test_username)
            assert user_info is not None, "User should exist after creation"
            assert user_info['username'] == test_username
            
        finally:
            # Cleanup
            manager.delete_user(test_username)

    def test_get_user_info(self, manager, test_username):
        """Test retrieving user information."""
        try:
            # Setup
            manager.create_user(test_username, "Pass123!", "default")
            
            # Test
            info = manager.get_user_info(test_username)
            
            # Verify
            assert info is not None
            assert 'username' in info
            assert 'used_bytes' in info
            assert 'status' in info
            assert 'connected_devices' in info
            assert 'is_active' in info
            
        finally:
            manager.delete_user(test_username)

    def test_reset_password(self, manager, test_username):
        """Test password reset functionality."""
        try:
            manager.create_user(test_username, "OldPass123!", "default")
            
            result = manager.reset_password(test_username, "NewPass456!")
            assert result is True, "Password reset should succeed"
            
        finally:
            manager.delete_user(test_username)

    def test_disable_user(self, manager, test_username):
        """Test disabling a user."""
        try:
            manager.create_user(test_username, "Pass123!", "default")
            
            result = manager.disable_user(test_username)
            assert result is True, "Disable should succeed"
            
            info = manager.get_user_info(test_username)
            assert info['status'] == 'banned', "User should be disabled"
            
        finally:
            manager.delete_user(test_username)

    def test_enable_user(self, manager, test_username):
        """Test enabling a disabled user."""
        try:
            manager.create_user(test_username, "Pass123!", "default")
            manager.disable_user(test_username)
            
            result = manager.enable_user(test_username)
            assert result is True, "Enable should succeed"
            
            info = manager.get_user_info(test_username)
            assert info['is_active'] is True, "User should be active"
            
        finally:
            manager.delete_user(test_username)

    def test_delete_user(self, manager, test_username):
        """Test complete user deletion."""
        # Create
        manager.create_user(test_username, "Pass123!", "default")
        
        # Delete
        result = manager.delete_user(test_username)
        assert result is True, "Deletion should succeed"
        
        # Verify
        info = manager.get_user_info(test_username)
        assert info is None, "User should not exist after deletion"

    def test_get_nonexistent_user(self, manager):
        """Test getting info for user that doesn't exist."""
        info = manager.get_user_info("nonexistent_user_12345")
        assert info is None, "Should return None for nonexistent user"
