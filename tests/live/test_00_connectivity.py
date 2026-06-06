"""Live connectivity checks against the test MikroTik router."""

import pytest

pytestmark = pytest.mark.live_mt


def test_connect_plain_api(mikrotik_manager):
    mikrotik_manager.connect()
    api = mikrotik_manager.api
    assert api is not None
    resource = api.get_resource("/system/resource")
    info = resource.get()
    assert info, "Expected /system/resource to return data"
    assert "version" in info[0] or "uptime" in info[0]


def test_user_manager_reachable(mikrotik_manager):
    mikrotik_manager.connect()
    user_api = mikrotik_manager._get_resource("/user-manager/user")
    users = user_api.get()
    assert users is not None


@pytest.mark.parametrize("mgr_fixture", ["mikrotik_manager_ssl"])
def test_connect_ssl_fallback(request, mt_config, mgr_fixture):
    """Optional: verify API-SSL on 443 when plain 8728 is blocked."""
    mgr = request.getfixturevalue(mgr_fixture)
    try:
        mgr.connect()
        api = mgr.api
        assert api is not None
    except Exception as exc:
        pytest.skip(f"API-SSL on port {mt_config['port_ssl']} not available: {exc}")
