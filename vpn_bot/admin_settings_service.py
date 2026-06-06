
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.utils import LanguageManager, get_currency_unit


def default_wallet_custom_limits(unit: str) -> tuple[float, float]:
    """Default min/max for custom wallet top-up by currency unit."""
    if unit == "TOMAN":
        return 50_000.0, 100_000_000.0
    if unit == "RIAL":
        return 500_000.0, 1_000_000_000.0
    return 5.0, 1000.0


def _coerce_bool(val, default: bool = True) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(val, (int, float)):
        return bool(val)
    return default


async def is_wallet_custom_amount_enabled() -> bool:
    return _coerce_bool(await get_admin_setting("wallet_custom_enabled", True), True)


async def set_wallet_custom_amount_enabled(enabled: bool) -> None:
    await set_admin_setting("wallet_custom_enabled", enabled)


async def get_wallet_custom_limits() -> tuple[float, float]:
    """Return (min, max) in the active currency unit."""
    unit = await get_currency_unit()
    dmin, dmax = default_wallet_custom_limits(unit)
    try:
        min_v = float(await get_admin_setting("wallet_custom_min", dmin))
    except (TypeError, ValueError):
        min_v = dmin
    try:
        max_v = float(await get_admin_setting("wallet_custom_max", dmax))
    except (TypeError, ValueError):
        max_v = dmax
    if min_v > max_v:
        min_v, max_v = max_v, min_v
    return min_v, max_v


async def set_wallet_custom_min(amount: float) -> tuple[bool, str | None]:
    """Set custom top-up minimum; returns (ok, error_key)."""
    _, max_v = await get_wallet_custom_limits()
    if amount < 1:
        return False, "invalid"
    if amount > max_v:
        return False, "min_above_max"
    await set_admin_setting("wallet_custom_min", amount)
    return True, None


async def set_wallet_custom_max(amount: float) -> tuple[bool, str | None]:
    """Set custom top-up maximum; returns (ok, error_key)."""
    min_v, _ = await get_wallet_custom_limits()
    if amount < 1:
        return False, "invalid"
    if amount < min_v:
        return False, "max_below_min"
    await set_admin_setting("wallet_custom_max", amount)
    return True, None

# --- Payment Cards ---
async def get_payment_cards():
    return await get_admin_setting('payment_cards', [])

async def add_payment_card(card_data: dict):
    cards = await get_payment_cards()
    cards.append(card_data)
    await set_admin_setting('payment_cards', cards)
    return True

async def delete_payment_card(index: int):
    cards = await get_payment_cards()
    if 0 <= index < len(cards):
        deleted = cards.pop(index)
        await set_admin_setting('payment_cards', cards)
        return True, deleted
    return False, None

# --- Wallet Presets ---
async def get_wallet_presets():
    return await get_admin_setting('wallet_presets', [5, 10, 20])

async def add_wallet_preset(amount: float):
    presets = await get_wallet_presets()
    if amount not in presets:
        presets.append(amount)
        presets.sort()
        await set_admin_setting('wallet_presets', presets)
        return True, "added"
    return False, "exists"

async def delete_wallet_preset(index: int):
    presets = await get_wallet_presets()
    if 0 <= index < len(presets):
        deleted = presets.pop(index)
        await set_admin_setting('wallet_presets', presets)
        return True, deleted
    return False, None

# --- Ticket Subjects ---
async def get_ticket_subjects():
    return await get_admin_setting('ticket_subjects', [])

async def add_ticket_subject(subject: str):
    subjects = await get_ticket_subjects()
    if subject not in subjects:
        subjects.append(subject)
        await set_admin_setting('ticket_subjects', subjects)
        return True, "added"
    return False, "exists"

async def delete_ticket_subject(index: int):
    subjects = await get_ticket_subjects()
    if 0 <= index < len(subjects):
        deleted = subjects.pop(index)
        await set_admin_setting('ticket_subjects', subjects)
        return True, deleted
    return False, None

async def reset_ticket_subjects():
    await set_admin_setting('ticket_subjects', [])
    return True

# --- Connection Info ---
async def get_server_connection_info(server_id: int):
    conn_info = await get_admin_setting('connection_info', {})
    return conn_info.get(str(server_id), {
        'l2tp': {'ip': '', 'port': '1701', 'secret': '123456', 'version': 'l2tp_v2'},
        'sstp': {'ip': '', 'port': '443'}
    })

async def update_server_connection_info(server_id: int, data: dict):
    conn_info = await get_admin_setting('connection_info', {})
    if str(server_id) not in conn_info:
        conn_info[str(server_id)] = {
            'l2tp': {'ip': '', 'port': '1701', 'secret': '123456', 'version': 'l2tp_v2'},
            'sstp': {'ip': '', 'port': '443'}
        }
    
    # Merge data
    for proto in ['l2tp', 'sstp']:
        if proto in data:
            for k, v in data[proto].items():
                conn_info[str(server_id)][proto][k] = v
                
    await set_admin_setting('connection_info', conn_info)
    return True

# --- Custom Messages ---
async def get_custom_message(key: str, default="Not set"):
    return await get_admin_setting(key, default)

async def set_custom_message(key: str, value: str):
    await set_admin_setting(key, value)
    return True

async def get_support_group_id():
    """Fetch support group ID from DB."""
    val = await get_admin_setting('support_group_id')
    return int(val) if val else None

async def set_support_group_id(group_id: int):
    """Save support group ID to DB."""
    await set_admin_setting('support_group_id', str(group_id))
    return True


async def get_receipt_group_id():
    """Fetch receipt notification group ID from DB."""
    val = await get_admin_setting('receipt_group_id')
    return int(val) if val else None


async def set_receipt_group_id(group_id: int):
    """Save receipt notification group ID to DB."""
    await set_admin_setting('receipt_group_id', str(group_id))
    return True


async def get_receipt_notif_mode() -> str:
    """Receipt notification mode: pv | group | both."""
    mode = await get_admin_setting('receipt_notif_mode', 'pv')
    return mode if mode in ('pv', 'group', 'both') else 'pv'


async def set_receipt_notif_mode(mode: str) -> None:
    """Update receipt notification mode."""
    if mode not in ('pv', 'group', 'both'):
        mode = 'pv'
    await set_admin_setting('receipt_notif_mode', mode)


# --- Purchase Terms ---

PURCHASE_TERMS_MODE_ONCE = "once"
PURCHASE_TERMS_MODE_EVERY = "every_purchase"


async def is_purchase_terms_enabled() -> bool:
    return _coerce_bool(await get_admin_setting("purchase_terms_enabled", False), False)


async def set_purchase_terms_enabled(enabled: bool) -> None:
    await set_admin_setting("purchase_terms_enabled", enabled)


async def get_purchase_terms_mode() -> str:
    mode = await get_admin_setting("purchase_terms_mode", PURCHASE_TERMS_MODE_ONCE)
    return mode if mode in (PURCHASE_TERMS_MODE_ONCE, PURCHASE_TERMS_MODE_EVERY) else PURCHASE_TERMS_MODE_ONCE


async def set_purchase_terms_mode(mode: str) -> None:
    if mode not in (PURCHASE_TERMS_MODE_ONCE, PURCHASE_TERMS_MODE_EVERY):
        mode = PURCHASE_TERMS_MODE_ONCE
    await set_admin_setting("purchase_terms_mode", mode)


async def get_purchase_terms_version() -> str:
    return str(await get_admin_setting("purchase_terms_version", "1"))


async def _bump_purchase_terms_version() -> str:
    current = await get_purchase_terms_version()
    try:
        new_v = str(int(current) + 1)
    except (TypeError, ValueError):
        new_v = "2"
    await set_admin_setting("purchase_terms_version", new_v)
    return new_v


def _resolve_localized_text(val, default: str) -> str:
    if isinstance(val, dict):
        lang = LanguageManager._current_lang
        return val.get(lang, val.get("en", default))
    if val and str(val).strip() and str(val) != "Not set":
        return str(val)
    return default


async def get_purchase_terms_text() -> str:
    default = LanguageManager.get("buy.terms_default_text")
    raw = await get_admin_setting("purchase_terms_text")
    return _resolve_localized_text(raw, default)


async def set_purchase_terms_text(value: str) -> str:
    await set_admin_setting("purchase_terms_text", value)
    return await _bump_purchase_terms_version()
