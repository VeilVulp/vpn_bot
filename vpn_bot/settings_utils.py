import json
from datetime import datetime
from sqlalchemy import select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import AdminSetting

# Simple TTL Cache for settings
_SETTINGS_CACHE = {}
_CACHE_TTL = 60 # 60 seconds is enough for high-speed responsiveness

async def get_admin_setting(key: str, default=None):
    """Get admin setting value with TTL caching."""
    now = datetime.now().timestamp()
    
    # Check cache
    if key in _SETTINGS_CACHE:
        val, expiry = _SETTINGS_CACHE[key]
        if now < expiry:
            return val

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminSetting).where(AdminSetting.key == key)
        )
        setting = result.scalars().first()
        
        final_val = default
        if setting:
            try:
                if setting.value_json is not None:
                     final_val = setting.value_json
                else:
                     final_val = json.loads(setting.value) if setting.value else default
            except:
                final_val = setting.value or default
        
        # Update cache
        _SETTINGS_CACHE[key] = (final_val, now + _CACHE_TTL)
        return final_val

async def set_admin_setting(key: str, value):
    """Set admin setting value."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminSetting).where(AdminSetting.key == key)
        )
        setting = result.scalars().first()
        
        value_str = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        
        if setting:
            setting.value = value_str
            setting.updated_at = datetime.now()
        else:
            setting = AdminSetting(key=key, value=value_str)
            session.add(setting)
        
        await session.commit()
        
        # Invalidate cache
        if key in _SETTINGS_CACHE:
            _SETTINGS_CACHE.pop(key, None)
