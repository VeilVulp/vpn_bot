"""
Full admin panel E2E matrix — runs crawl + domain tests and writes JSON report.
"""

from __future__ import annotations

import pytest

from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application
from tests.live.helpers.admin_e2e_report import new_report, record_section, write_report
from vpn_bot.admin_management import full_permission_preset, limited_permission_preset

pytest_plugins = ["tests.live.admin_e2e_conftest"]

pytestmark = [pytest.mark.live_mt, pytest.mark.live_admin_e2e]


@pytest.mark.asyncio
async def test_admin_panel_full_matrix(admin_driver, live_server, e2e_test_user):
    """Orchestrated smoke: menu crawl + key domains; records report JSON."""
    report = new_report()

    try:
        crawl = await admin_driver.bfs_crawl(max_depth=2)
        record_section(
            report,
            "menu_bfs_crawl",
            ok=not admin_driver.errors and len(crawl.errors) == 0,
            detail={"taps": len(crawl.taps), "visited": len(crawl.visited), "errors": crawl.errors},
        )
    except Exception as exc:
        record_section(report, "menu_bfs_crawl", ok=False, error=str(exc))

    sections = [
        ("main_and_reports", lambda: _run_main_reports(admin_driver)),
        ("user_search", lambda: _run_user_search(admin_driver, e2e_test_user)),
        ("servers", lambda: _run_servers(admin_driver, live_server)),
        ("tickets_menu", lambda: _run_tickets(admin_driver)),
        ("submenus", lambda: _run_submenus(admin_driver)),
    ]

    for name, fn in sections:
        try:
            await fn()
            record_section(report, name, ok=True)
        except Exception as exc:
            record_section(report, name, ok=False, error=str(exc))

    try:
        rbac_report = await _run_rbac_matrix_profiles()
        record_section(
            report,
            "rbac_access_matrix",
            ok=rbac_report["fail"] == 0,
            detail=rbac_report,
        )
    except Exception as exc:
        record_section(report, "rbac_access_matrix", ok=False, error=str(exc))

    path = write_report(report)
    assert report["fail"] == 0, f"Report failures: {report['errors']} (see {path})"


async def _run_rbac_matrix_profiles() -> dict:
    """Offline RBAC crawl embedded in live orchestrator report."""
    from vpn_bot.config import config

    app, bot, _ = await build_admin_application(include_user_handlers=False)
    uid = int(config.ADMIN_IDS[0]) if config.ADMIN_IDS else 900001
    profiles = {
        "super": (True, full_permission_preset()),
        "db_full": (False, full_permission_preset()),
        "db_limited": (False, limited_permission_preset()),
    }
    out = {"profiles": {}, "fail": 0, "errors": []}
    for name, (is_super, perms) in profiles.items():
        driver = AdminE2EDriver(
            app,
            bot,
            uid if is_super else uid + 50_000 + hash(name) % 1000,
            permissions=perms,
            super_admin=is_super,
        )
        try:
            if name == "super":
                crawl = await driver.bfs_crawl(max_depth=1)
            else:
                crawl = await driver.crawl_allowed_only(max_depth=1)
            out["profiles"][name] = {
                "taps": len(crawl.taps),
                "errors": crawl.errors,
                "handler_errors": len(driver.errors),
            }
            if crawl.errors or driver.errors:
                out["fail"] += 1
        except Exception as exc:
            out["profiles"][name] = {"error": str(exc)}
            out["fail"] += 1
            out["errors"].append(f"{name}: {exc}")
    return out


async def _run_main_reports(driver):
    await driver.open_admin_menu()
    await driver.tap("admin_reports")


async def _run_user_search(driver, user):
    await driver.open_admin_menu()
    await driver.tap("search_user")
    await driver.send_text(str(user.telegram_id))


async def _run_servers(driver, live_server):
    await driver.open_admin_menu()
    await driver.tap("list_servers")
    assert driver.callbacks_on_screen() or not driver.errors


async def _run_tickets(driver):
    await driver.open_admin_menu()
    await driver.tap("admin_tickets")


async def _run_submenus(driver):
    await driver.open_admin_menu()
    for cb in ("wg_mgmt_menu", "ovpn_l2tp_mgmt_menu", "sales_mgmt_menu", "bot_config_menu"):
        await driver.tap("admin_start")
        await driver.tap(cb)
