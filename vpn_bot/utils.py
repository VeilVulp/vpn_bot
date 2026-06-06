import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from telegram import Update
from urllib.parse import quote

def setup_logger(name: str = "vpn_bot", log_file: str = "bot.log", level: int = logging.INFO) -> logging.Logger:
    """
    Setup a comprehensive logger with console and file output.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

# Global logger instance
logger = setup_logger()

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IRAN_TZ = ZoneInfo("Asia/Tehran")

# Global lock to prevent concurrent DB maintenance (e.g. pg_dump vs cleanup)
db_maintenance_lock = asyncio.Lock()

_mikrotik_semaphore: asyncio.Semaphore | None = None


def get_mikrotik_semaphore() -> asyncio.Semaphore:
    """Limit concurrent blocking MikroTik API calls (shared thread pool)."""
    global _mikrotik_semaphore
    if _mikrotik_semaphore is None:
        import os

        n = int(os.getenv("MIKROTIK_MAX_CONCURRENT", "10"))
        _mikrotik_semaphore = asyncio.Semaphore(max(1, n))
    return _mikrotik_semaphore


class MikroTikBusyError(Exception):
    """Raised when the global MikroTik semaphore cannot be acquired in time."""


async def run_mikrotik(func, *args, **kwargs):
    """Run a sync MikroTik callable in a thread with global concurrency cap."""
    import os

    sem = get_mikrotik_semaphore()
    wait = float(os.getenv("MIKROTIK_SEMAPHORE_WAIT", "25"))
    try:
        await asyncio.wait_for(sem.acquire(), timeout=wait)
    except asyncio.TimeoutError as exc:
        raise MikroTikBusyError("MikroTik API busy (too many concurrent requests)") from exc
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    finally:
        sem.release()


def clear_user_processing(context) -> None:
    """Release safe_response lock if a prior handler did not clear it."""
    if context and getattr(context, "user_data", None) is not None:
        context.user_data.pop("is_processing", None)


_WG_CONV_CALLBACK_PREFIXES = (
    "set_wg_",
    "man_wg_",
    "wg_edit_",
    "wg_reapply",
    "wg_migrate",
    "wg_delete",
    "wg_notify",
    "back_to_wg_section",
    "back_to_int_settings",
    "edit_wg_interface_",
)


def wg_conversation_active(context) -> bool:
    """True while admin WG interface create/edit ConversationHandler is active."""
    if not context or not getattr(context, "user_data", None):
        return False
    return bool(
        context.user_data.get("edit_wg_interface_id")
        or context.user_data.get("new_wg_iface_data")
    )


def _is_wg_conversation_callback(data: str) -> bool:
    if not data:
        return False
    return data.startswith(_WG_CONV_CALLBACK_PREFIXES)


async def notify_user_busy(update: Update, context) -> bool:
    """If user already has an in-flight handler, show alert. Returns True when busy."""
    if not context or not context.user_data.get("is_processing"):
        return False
    if update.callback_query:
        try:
            await update.callback_query.answer(
                LanguageManager.get("utils.safe_loading"),
                show_alert=True,
            )
        except Exception:
            pass
    elif update.message:
        try:
            await update.message.reply_text(LanguageManager.get("utils.safe_loading"))
        except Exception:
            pass
    return True


def utc_now() -> datetime:
    """Timezone-aware UTC now for DB expiry comparisons."""
    return datetime.now(timezone.utc)


def to_iran_local(dt: datetime) -> datetime:
    """Convert stored UTC (or naive-as-UTC) datetime to Asia/Tehran for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IRAN_TZ)

# --- WireGuard Utils ---
import base64
import string

def to_base36(n: int) -> str:
    """Convert an integer to a base36 string (0-9, a-z)."""
    chars = string.digits + string.ascii_lowercase
    if n == 0:
        return '0'
    res = ''
    while n > 0:
        n, r = divmod(n, 36)
        res = chars[r] + res
    return res

from cryptography.hazmat.primitives.asymmetric import x25519
import qrcode
from io import BytesIO

def generate_wg_keys():
    """Generate WireGuard private and public keys using Curve25519."""
    private_key = x25519.X25519PrivateKey.generate()
    public_key = private_key.public_key()
    
    # Export keys to base64
    priv_bytes = private_key.private_bytes_raw()
    pub_bytes = public_key.public_bytes_raw()
    
    priv_b64 = base64.b64encode(priv_bytes).decode('utf-8')
    pub_b64 = base64.b64encode(pub_bytes).decode('utf-8')
    
    return priv_b64, pub_b64

def generate_wg_qr(config_text: str):
    """Generate a QR code image for the WireGuard config."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(config_text)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format='PNG')
    bio.seek(0)
    return bio

def generate_wg_conf(private_key, address, server_pub_key, endpoint, dns="1.1.1.1", mtu=1420, keepalive=25):
    """Format WireGuard .conf file content."""
    conf = f"""[Interface]
PrivateKey = {private_key}
Address = {address}
DNS = {dns}
MTU = {mtu}

[Peer]
PublicKey = {server_pub_key}
AllowedIPs = 0.0.0.0/0
"""
    conf += f"Endpoint = {endpoint}\n"
    conf += f"PersistentKeepalive = {keepalive}\n"
    
    return conf

def generate_wg_url(private_key, address, server_pub_key, endpoint, dns="8.8.8.8", mtu=1420, keepalive=25, name="WireGuard"):
    """
    Generate a WireGuard URL (wg://) suitable for clients like Hiddify.
    Format: wg://ip:port?publicKey=...&privateKey=...&ip=...&dns=...&mtu=...&keepalive=...#Name
    """
    # Parse endpoint
    if ':' in endpoint:
        host, port = endpoint.split(':')
    else:
        host = endpoint
        port = "51820"
    
    # Clean address (remove CIDR)
    ip_clean = address.split('/')[0]

    params = [
        f"publicKey={quote(server_pub_key)}",
        f"privateKey={quote(private_key)}",
        f"ip={quote(ip_clean)}",
        f"dns={quote(dns)}",
        f"mtu={mtu}",
        f"keepalive={keepalive}",
        "udp=1"
    ]
    query_string = "&".join(params)
    return f"wg://{host}:{port}?{query_string}#{quote(name)}"

# --- Security: Rate Limiting ---
from functools import wraps
import time
import logging

_rate_log = logging.getLogger("vpn_bot.rate_limit")
user_last_action = {}
_redis_client = None


def _get_redis_client():
    """Lazy-init Redis client when REDIS_URL is configured."""
    global _redis_client
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.from_url(url, decode_responses=True)
            _redis_client.ping()
            _rate_log.info("Rate limiting backed by Redis")
        except Exception as exc:
            _rate_log.warning("Redis unavailable for rate limit (%s); using in-memory", exc)
            _redis_client = False  # sentinel: tried and failed
    return _redis_client if _redis_client is not False else None


def _redis_rate_limited(user_id: int, seconds: float) -> bool:
    """Return True when the user should be blocked (within cooldown window)."""
    client = _get_redis_client()
    if not client:
        return False
    key = f"vpn_bot:rl:{user_id}"
    try:
        # SET NX with TTL — first call wins; subsequent calls within TTL are blocked
        if client.set(key, "1", nx=True, ex=max(1, int(seconds))):
            return False
        return True
    except Exception as exc:
        _rate_log.debug("Redis rate limit error: %s", exc)
        return False


async def enforce_rate_limit(update, seconds: float = 3) -> bool:
    """Return True when the user hit the rate limit (request should be dropped)."""
    if not update or not update.effective_user:
        return False
    user_id = update.effective_user.id
    if _redis_rate_limited(user_id, seconds):
        if update.callback_query:
            await update.callback_query.answer(LanguageManager.get('utils.rate_limit_callback'), show_alert=True)
        elif update.message:
            await update.message.reply_text(LanguageManager.get('utils.rate_limit_message'))
        return True
    current_time = time.time()
    if user_id in user_last_action:
        elapsed = current_time - user_last_action[user_id]
        if elapsed < seconds:
            if update.callback_query:
                await update.callback_query.answer(LanguageManager.get('utils.rate_limit_callback'), show_alert=True)
            elif update.message:
                await update.message.reply_text(LanguageManager.get('utils.rate_limit_message'))
            return True
    user_last_action[user_id] = current_time
    return False


def rate_limit(seconds=2):
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            if await enforce_rate_limit(update, seconds):
                return
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

def safe_response(func):
    """
    Prevents concurrent requests from the same user and shows a loading state.
    During WG interface edit/create, skips the loading overlay so wizard steps keep UI.
    """
    @wraps(func)
    async def wrapper(update: Update, context, *args, **kwargs):
        if not update or not update.effective_user:
            return await func(update, context, *args, **kwargs)

        data = (update.callback_query.data or "") if update.callback_query else ""
        light = bool(
            update.callback_query
            and wg_conversation_active(context)
            and _is_wg_conversation_callback(data)
        )

        if await notify_user_busy(update, context):
            return

        context.user_data["is_processing"] = True

        if update.callback_query:
            try:
                await update.callback_query.answer()
                if not light:
                    await update.callback_query.message.edit_text(
                        LanguageManager.get("utils.safe_loading"),
                        reply_markup=None,
                        parse_mode="Markdown",
                    )
            except Exception as e:
                logger.debug(f"SafeResponse: Could not edit for loading state: {e}")

        try:
            return await func(update, context, *args, **kwargs)
        finally:
            context.user_data.pop("is_processing", None)

    return wrapper

# --- Security: Input Validation ---
import re
import json

async def check_maintenance_status(server_id: int = None, interface_id: int = None) -> tuple[bool, str | None]:
    """
    Check if the system or a specific server/interface is under maintenance.
    Returns: (is_blocked: bool, message: str or None)
    """
    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.admin_settings import get_admin_setting
    from vpn_bot.models import Server, WireGuardInterface
    
    async with AsyncSessionLocal() as session:
        # 1. Global Maintenance
        global_maint = await get_admin_setting('system_maintenance_active', False)
        if global_maint:
            msg = await get_admin_setting('maintenance_mode_msg', LanguageManager.get('admin.sales.maintenance_mode_msg_default'))
            return True, msg
            
        # 2. Server Maintenance
        if server_id:
            server = await session.get(Server, server_id)
            if server and not server.is_active:
                return True, LanguageManager.get('admin.sales.maintenance_mode_msg')
                
        # 3. Interface Maintenance
        if interface_id:
            iface = await session.get(WireGuardInterface, interface_id)
            if iface and not iface.is_active:
                return True, LanguageManager.get('admin.sales.maintenance_mode_msg')
                
    return False, None

def validate_amount(amount_str: str) -> float:
    try:
        val = float(amount_str)
        if 5.0 <= val <= 1000.0:
            return val
        return 0.0
    except ValueError:
        return 0.0

def sanitize_username(username: str) -> str:
    # Allow a-z, 0-9, and _
    return re.sub(r'[^a-zA-Z0-9_]', '', username)

def parse_duration_to_seconds(duration_str: str) -> int:
    """
    Parse a duration string into seconds.
    Supported formats: '30m' (minutes), '6h' (hours), '3d' (days), '2M' (months).
    Default unit is hours if no unit is specified (for backward compatibility).
    Returns 0 if invalid.
    """
    if duration_str is None:
        return 0
    
    # If it's already an int (from DB legacy), treat as hours
    if isinstance(duration_str, int):
        return duration_str * 3600
        
    s = str(duration_str).strip()
    
    # 1. Check for suffix (case-sensitive: m=minutes, h=hours, d=days, M=months)
    match = re.match(r"^(\d+(\.\d+)?)\s*(m|h|d|M)$", s)
    if match:
        val = float(match.group(1))
        unit = match.group(3)
        if unit == 'm':
            return int(val * 60)
        if unit == 'h':
            return int(val * 3600)
        if unit == 'd':
            return int(val * 86400)
        if unit == 'M':
            return int(val * 2592000)  # 30 days
        
    # 2. Handle pure numeric (assume hours for legacy compatibility)
    try:
        return int(float(s) * 3600)
    except:
        return 0

def format_seconds_human(seconds: int) -> str:
    """Convert seconds to human-readable duration string."""
    if seconds <= 0:
        return "0"
    if seconds >= 2592000 and seconds % 2592000 == 0:
        return f"{seconds // 2592000}M"
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"

async def get_currency_unit():
    """Get current currency unit (USD, TOMAN, RIAL) via cached admin settings."""
    from vpn_bot.settings_utils import get_admin_setting

    val = await get_admin_setting("currency_unit", "USD")
    if isinstance(val, str):
        return val.strip('"').strip("'")
    return "USD"

async def get_profile_price(profile):
    """Pick the correct price from Profile model based on active currency."""
    unit = await get_currency_unit()
    if unit == 'USD':
        return profile.price_usd
    elif unit == 'TOMAN':
        return profile.price_toman
    elif unit == 'RIAL':
        # Rial is Toman * 10 as per user request
        return profile.price_toman * 10
    return profile.price_usd


async def get_wg_profile_price(profile):
    """Pick the correct price from WireGuardProfile based on active currency."""
    unit = await get_currency_unit()
    if unit == 'USD':
        return float(profile.price_usd or 0)
    if unit == 'TOMAN':
        return float(profile.price_toman or 0)
    if unit == 'RIAL':
        if profile.price_rial:
            return float(profile.price_rial)
        return float(profile.price_toman or 0) * 10
    return float(profile.price_usd or 0)


async def resolve_checkout_price(
    profile,
    *,
    is_wg: bool = False,
    coupon_id: int | None = None,
    user_id: int | None = None,
    context: str = "purchase_ovpn",
):
    """Compute base/final price with optional coupon preview (no redemption)."""
    from sqlalchemy import select
    from vpn_bot.models import User
    from vpn_bot.database import AsyncSessionLocal

    base = await (get_wg_profile_price(profile) if is_wg else get_profile_price(profile))
    unit = await get_currency_unit()
    if coupon_id is None or user_id is None:
        from vpn_bot.discount_service import PricingResult
        return PricingResult(
            base_amount=base,
            discount_amount=0.0,
            final_amount=base,
            currency_unit=unit,
        )
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.id == user_id))
        user = u_res.scalars().first()
        if not user:
            from vpn_bot.discount_service import PricingResult
            return PricingResult(
                base_amount=base,
                discount_amount=0.0,
                final_amount=base,
                currency_unit=unit,
            )
        from vpn_bot.discount_service import apply_coupon_to_amount
        return await apply_coupon_to_amount(
            session,
            user_id=user.id,
            base_amount=base,
            currency=unit,
            context=context,
            coupon_id=coupon_id,
        )

async def format_currency(amount: float, unit: str = None) -> str:
    """Format amount based on active currency or provided unit."""
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        amount = 0.0
        
    if unit is None:
        unit = await get_currency_unit()
    
    if unit == 'TOMAN':
        label = LanguageManager.get('common.toman')
        if label == '[common.toman]': label = 'تومان'
        return f"{amount:,.0f} {label}"
    elif unit == 'RIAL':
        label = LanguageManager.get('common.rial')
        if label == '[common.rial]': label = 'ریال'
        return f"{amount:,.0f} {label}"
    else:
        # Default to USD
        return f"${amount:,.2f}"

RLM = "\u200f"
LRM = "\u200e"
_ARABIC_PERSIAN_RE = __import__("re").compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]"
)
_LTR_TOKEN_RE = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9_./:\-\s]*[A-Za-z0-9]|[A-Za-z0-9]")
_LEADING_EMOJI_RE = __import__("re").compile(
    r"^([\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F600-\U0001F64F"
    r"\U0001F680-\U0001F6FF\U00002300-\U000023FF\U0000203C-\U00002049"
    r"\U00002194-\U00002199\U00002B50\U00002764\U0000FE0F"
    r"\U0001F1E0-\U0001F1FF]+\s*)+"
)


def _line_has_rtl_script(line: str) -> bool:
    return bool(_ARABIC_PERSIAN_RE.search(line))


def _move_leading_emoji_to_end(line: str, *, force_rtl: bool = False) -> str:
    if not force_rtl and not _line_has_rtl_script(line):
        return line
    m = _LEADING_EMOJI_RE.match(line)
    if not m:
        return line
    prefix = m.group(0).strip()
    rest = line[m.end():].strip()
    if not rest:
        return line
    return f"{rest} {prefix}"


def _isolate_ltr_tokens(line: str, *, force_rtl: bool = False) -> str:
    if LRM in line:
        return line
    if not force_rtl and not _line_has_rtl_script(line):
        return line

    def wrap(match):
        seg = match.group(0)
        digits_only = seg.replace(",", "").replace(".", "").replace(" ", "")
        if digits_only.isdigit():
            return seg
        return f"{LRM}{seg}{LRM}"

    return _LTR_TOKEN_RE.sub(wrap, line)


def _format_rtl_line(line: str, *, force_rtl: bool = False) -> str:
    stripped = line.strip()
    if not stripped:
        return line
    if not force_rtl and not _line_has_rtl_script(stripped):
        return line

    body = stripped.lstrip(RLM)
    if stripped.startswith(RLM) and not _LEADING_EMOJI_RE.match(body):
        if force_rtl or _line_has_rtl_script(stripped):
            return stripped

    body = _move_leading_emoji_to_end(body, force_rtl=force_rtl)
    body = _isolate_ltr_tokens(body, force_rtl=force_rtl)
    if not body.startswith(RLM):
        body = RLM + body
    return body


def format_telegram_rtl(text: str, *, lang: str | None = None) -> str:
    """Apply Unicode bidi marks so Persian messages render closer to RTL in Telegram."""
    if not text:
        return text
    if lang is None:
        lang = LanguageManager._current_lang
    if lang != "fa":
        return text
    return "\n".join(_format_rtl_line(line, force_rtl=True) for line in text.split("\n"))


def ensure_telegram_text(text, fallback_key: str = "common.error", **fmt_kwargs) -> str:
    """Return non-empty text for Telegram send/edit APIs; never pass blank strings."""
    out = text if text is not None else ""
    if not str(out).strip():
        out = LanguageManager.get(fallback_key, **fmt_kwargs)
    if not str(out).strip():
        out = LanguageManager.get("common.error")
    out = str(out)
    return format_telegram_rtl(out)


def ensure_telegram_html(text, fallback_key: str = "common.error", **fmt_kwargs) -> str:
    """Like ensure_telegram_text but preserves HTML tags (no RTL line rewriting)."""
    out = text if text is not None else ""
    if not str(out).strip():
        out = LanguageManager.get(fallback_key, **fmt_kwargs)
    if not str(out).strip():
        out = LanguageManager.get("common.error")
    return str(out)


def markdown_bold_to_html(text: str) -> str:
    """Convert legacy Markdown **bold** segments to Telegram HTML <b> tags."""
    from html import escape

    parts = str(text).split("**")
    out: list[str] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        escaped = escape(part)
        out.append(f"<b>{escaped}</b>" if i % 2 == 1 else escaped)
    return "".join(out)


async def send_localized_text(
    update: Update,
    text: str,
    reply_markup=None,
    parse_mode: str = "Markdown",
    *,
    query=None,
) -> None:
    """Send or edit a localized message with RTL formatting for fa."""
    if parse_mode == "HTML":
        body = ensure_telegram_html(text)
    else:
        body = ensure_telegram_text(text)
    q = query if query is not None else (update.callback_query if update else None)
    if q:
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.edit_text(body, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            await q.message.reply_text(body, reply_markup=reply_markup, parse_mode=parse_mode)
    elif update and update.message:
        await update.message.reply_text(body, reply_markup=reply_markup, parse_mode=parse_mode)


async def format_datetime(dt, include_time=True):
    """Format datetime for user display (Iran timezone; Shamsi calendar for FA)."""
    if not dt:
        return LanguageManager.get("common.na")

    local_dt = to_iran_local(dt)

    lang = LanguageManager._current_lang
    if lang == 'fa':
        try:
            import jdatetime

            wall = local_dt.replace(tzinfo=None)
            j_dt = jdatetime.datetime.fromgregorian(datetime=wall)
            if include_time:
                return j_dt.strftime('%Y/%m/%d %H:%M')
            return j_dt.strftime('%Y/%m/%d')
        except Exception:
            return local_dt.strftime('%Y-%m-%d %H:%M') if include_time else local_dt.strftime('%Y-%m-%d')
    else:
        return local_dt.strftime('%Y-%m-%d %H:%M') if include_time else local_dt.strftime('%Y-%m-%d')


# --- Internationalization (i18n) ---

class LanguageManager:
    _strings = {}
    _current_lang = 'en'
    _loaded = False
    _lang_cache_expiry: float = 0.0
    _LANG_CACHE_TTL = 60.0  # seconds; invalidated on admin language change
    
    @classmethod
    def load_locales(cls, locale_dir=None):
        """Load all JSON files from the locales directory."""
        if locale_dir is None:
            from vpn_bot._paths import LOCALES_DIR
            locale_dir = str(LOCALES_DIR)
        if not os.path.exists(locale_dir):
            os.makedirs(locale_dir, exist_ok=True)
            return

        for filename in os.listdir(locale_dir):
            if filename.endswith('.json'):
                lang_code = filename.split('.')[0]
                try:
                    with open(os.path.join(locale_dir, filename), 'r', encoding='utf-8') as f:
                        cls._strings[lang_code] = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to load locale {filename}: {e}")
        
        cls._loaded = True
        logger.info(f"Loaded languages: {list(cls._strings.keys())}")

    @classmethod
    def invalidate_language_cache(cls):
        """Force next refresh_language to read from DB (e.g. after admin changes language)."""
        cls._lang_cache_expiry = 0.0

    @classmethod
    async def refresh_language(cls, *, force: bool = False):
        """Fetch system language from DB (TTL-cached to avoid a query per Telegram update)."""
        import time
        from vpn_bot.settings_utils import get_admin_setting

        now = time.time()
        if not force and now < cls._lang_cache_expiry:
            return
        try:
            val = await get_admin_setting("system_language", "en")
            if isinstance(val, str):
                cls._current_lang = val.strip('"').strip("'")
            else:
                cls._current_lang = "en"
            cls._lang_cache_expiry = now + cls._LANG_CACHE_TTL
            logger.debug("LanguageManager: system language '%s'", cls._current_lang)
        except Exception as e:
            logger.error(f"LanguageManager: Failed to refresh language: {e}")
            cls._current_lang = "en"
            cls._lang_cache_expiry = now + cls._LANG_CACHE_TTL

    @classmethod
    async def global_refresh_handler(cls, update: Update, context):
        """Middleware-like handler to ensure language is up to date on every request."""
        await cls.refresh_language()

    @classmethod
    def get(cls, key: str, **kwargs) -> str:
        """
        Get a localized string by dot-notation key (e.g., 'common.welcome').
        Supports variable formatting via kwargs.
        """
        if not cls._loaded:
            cls.load_locales()
            
        lang = cls._current_lang
        
        # Helper to traverse dict
        def lookup(d, keys):
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k)
                else:
                    return None
            return d

        keys = key.split('.')
        
        # Try current language
        val = lookup(cls._strings.get(lang, {}), keys)
        
        # Fallback to English if not found
        if val is None and lang != 'en':
            val = lookup(cls._strings.get('en', {}), keys)
            
        if val is None:
            return f"[{key}]"
            
        try:
            # Custom handling for balance formatting
            if 'balance' in kwargs:
                try:
                    kwargs['balance'] = float(kwargs['balance'])
                except:
                    pass
            return val.format(**kwargs)
        except Exception:
            return val

    @classmethod
    def get_all_translations(cls, key: str) -> list:
        """Get all translations for a key to use in regex filters (escaped)."""
        import re
        return [re.escape(v) for v in cls.get_all_translations_raw(key)]

    @classmethod
    def get_all_translations_raw(cls, key: str) -> list:
        """Get all translations for a key as raw strings."""
        if not cls._loaded:
            cls.load_locales()
            
        values = []
        keys = key.split('.')
        
        def lookup(d, keys):
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k)
                else:
                    return None
            return d

        for lang in cls._strings:
            val = lookup(cls._strings[lang], keys)
            if val and isinstance(val, str):
                values.append(val)
        return values
