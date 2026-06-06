
from sqlalchemy import select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import OvpnConfig, Server

async def get_all_ovpn_configs():
    """Fetch all OVPN configs with server names."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(OvpnConfig, Server.name)
            .outerjoin(Server, OvpnConfig.server_id == Server.id)
        )
        return result.all()

async def get_ovpn_config_by_id(config_id: int):
    """Fetch a specific OVPN config."""
    async with AsyncSessionLocal() as session:
        return await session.get(OvpnConfig, config_id)

async def create_ovpn_config(data: dict):
    """Create a new OVPN config."""
    async with AsyncSessionLocal() as session:
        new_cfg = OvpnConfig(
            server_id=data.get('server_id'),
            config_content=data['content'],
            filename=data['filename'],
            display_name=data.get('display_name')
        )
        session.add(new_cfg)
        await session.commit()
        return new_cfg

async def update_ovpn_config(config_id: int, data: dict):
    """Update an existing OVPN config."""
    async with AsyncSessionLocal() as session:
        cfg = await session.get(OvpnConfig, config_id)
        if not cfg: return False
        
        if 'content' in data: cfg.config_content = data['content']
        if 'filename' in data: cfg.filename = data['filename']
        if 'display_name' in data: cfg.display_name = data['display_name']
        if 'server_id' in data: cfg.server_id = data['server_id']
        
        await session.commit()
        return True

async def delete_ovpn_config(config_id: int):
    """Delete an OVPN config."""
    async with AsyncSessionLocal() as session:
        cfg = await session.get(OvpnConfig, config_id)
        if not cfg: return False
        
        await session.delete(cfg)
        await session.commit()
        return True
