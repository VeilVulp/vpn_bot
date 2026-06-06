from sqlalchemy import select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile, Server
from vpn_bot.utils import logger
from vpn_bot.mikrotik_manager import get_mikrotik_manager
import asyncio

async def get_all_profiles():
    """Fetch all active profiles."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Profile).where(Profile.is_active))
        return result.scalars().all()

async def get_profile_by_id(profile_id: int):
    """Fetch a specific profile by ID."""
    async with AsyncSessionLocal() as session:
        return await session.get(Profile, profile_id)

async def update_profile(profile_id: int, data: dict):
    """Update an existing profile and propagate if needed."""
    async with AsyncSessionLocal() as session:
        prof = await session.get(Profile, profile_id)
        if not prof: return False
        
        old_rate = prof.rate_limit
        if 'name' in data: prof.name = data['name']
        if 'price_toman' in data:
            prof.price_toman = data['price_toman']
            prof.price_rial = prof.price_toman * 10
        if 'rate_limit' in data: prof.rate_limit = data['rate_limit']
        
        if prof.rate_limit != old_rate:
            await _propagate_ovpn_speed_update(session, prof, prof.rate_limit)
            
        await session.commit()
        return True

async def _propagate_ovpn_speed_update(session, profile: Profile, new_speed: str):
    """Update OpenVPN UM profiles on the router."""
    server = await session.get(Server, profile.server_id)
    if server:
        mgr = get_mikrotik_manager(server)
        await asyncio.to_thread(mgr.connect)
        try:
            await asyncio.to_thread(mgr.create_profile_with_limits, 
                profile.name, profile.validity_days, 
                profile.data_limit_gb, new_speed)
        finally:
            await asyncio.to_thread(mgr.close)

async def create_profile_full(data: dict):
    """Create a new profile in DB and on MikroTik."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, data['server_id'])
        if not server: return None, "Server not found"
        
        mgr = get_mikrotik_manager(server)
        rate_limit = data.get('rate_limit')
        
        # MikroTik Propagation (connect() returns None on success — do not use `if success:`)
        try:
            await asyncio.to_thread(mgr.connect)
            mt_ok = await asyncio.to_thread(
                mgr.create_profile_with_limits,
                data['name'],
                data['days'],
                data['limit'],
                rate_limit,
            )
            if not mt_ok:
                return None, "MikroTik profile creation failed"
        except Exception as e:
            logger.error(f"MikroTik profile creation error: {e}")
            return None, f"MikroTik Error: {e}"
        finally:
            await asyncio.to_thread(mgr.close)
        
        new_prof = Profile(
            name=data['name'],
            validity_days=data['days'],
            data_limit_gb=data['limit'],
            price_usd=data.get('price_usd', 0),
            price_toman=data.get('price_toman', 0),
            rate_limit=rate_limit,
            server_id=server.id,
            is_active=True
        )
        session.add(new_prof)
        await session.commit()
        return new_prof, None

async def delete_profile_full(profile_id: int):
    """Delete profile from DB and MikroTik."""
    async with AsyncSessionLocal() as session:
        prof = await session.get(Profile, profile_id)
        if not prof: return False, "Profile not found"
        
        # Check for active subs
        from vpn_bot.models import Subscription
        res = await session.execute(select(Subscription).where(Subscription.profile_id == profile_id, Subscription.status == 'active'))
        if res.scalars().first():
            prof.is_active = False
            await session.commit()
            return True, "Archived (active subscriptions exist)"

        # MikroTik Cleanup
        server = await session.get(Server, prof.server_id)
        if server:
            mgr = get_mikrotik_manager(server)
            try:
                await asyncio.to_thread(mgr.connect)
                lim_api = mgr._get_resource('/user-manager/limitation')
                prof_api = mgr._get_resource('/user-manager/profile')
                pl_api = mgr._get_resource('/user-manager/profile-limitation')
                
                links = pl_api.get(profile=prof.name)
                for link in links: pl_api.remove(id=link['id'])
                
                l = lim_api.get(name=f"lim_{prof.name}")
                if l: lim_api.remove(id=l[0]['id'])
                
                pr = prof_api.get(name=prof.name)
                if pr: prof_api.remove(id=pr[0]['id'])
            except: pass
            finally: await asyncio.to_thread(mgr.close)

        await session.delete(prof)
        await session.commit()
        return True, "Deleted"
