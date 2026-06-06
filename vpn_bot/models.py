from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, BigInteger, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from vpn_bot.database import Base
from cryptography.fernet import Fernet
import os

# Encryption Setup
def _load_encryption_key() -> str:
    key = os.getenv('ENCRYPTION_KEY', '').strip()
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    dev_key = os.getenv('DEV_ENCRYPTION_KEY', '').strip()
    if key:
        return key
    if dev_key:
        import logging
        logging.getLogger("vpn_bot").warning(
            "Using DEV_ENCRYPTION_KEY — set ENCRYPTION_KEY for production"
        )
        return dev_key
    if debug:
        import logging
        logging.getLogger("vpn_bot").error(
            "ENCRYPTION_KEY missing with DEBUG=true — server passwords in DB "
            "were likely encrypted with a different ephemeral key from pytest. "
            "Set ENCRYPTION_KEY in .env (same value when using shared DB) or run "
            "scripts/fix_server_encryption.py"
        )
        raise RuntimeError(
            "ENCRYPTION_KEY required even in DEBUG when using PostgreSQL server rows"
        )
    raise RuntimeError(
        "ENCRYPTION_KEY must be set in environment (required for server password encryption)"
    )

KEY = _load_encryption_key()
cipher = Fernet(KEY.encode())

def encrypt_text(text: str) -> str:
    if not text: return ""
    return cipher.encrypt(text.encode()).decode()

def decrypt_text(text: str) -> str:
    if not text: return ""
    try:
        return cipher.decrypt(text.encode()).decode()
    except Exception:
        raise ValueError("Failed to decrypt stored secret — check ENCRYPTION_KEY")

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    wallet_balance = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True)
    is_banned = Column(Boolean, default=False)
    purchase_terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    purchase_terms_version = Column(String(20), nullable=True)
    
    subscriptions = relationship("Subscription", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    receipts = relationship("PaymentReceipt", back_populates="user")

class Server(Base):
    __tablename__ = "servers"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    host = Column(String, nullable=False)
    port = Column(Integer, default=8728)
    username = Column(String, nullable=False)
    _password = Column('password', String, nullable=False)
    is_active = Column(Boolean, default=True)
    location = Column(String, nullable=True)
    
    @property
    def password(self):
        return decrypt_text(self._password)
    
    @password.setter
    def password(self, val):
        self._password = encrypt_text(val)
    
    subscriptions = relationship("Subscription", back_populates="server")
    profiles = relationship("Profile", back_populates="server")

class Profile(Base):
    __tablename__ = "profiles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # MikroTik profile name
    version = Column(Integer, default=1)
    description = Column(String, nullable=True)
    price_usd = Column(Float, nullable=False, default=0.0)
    price_toman = Column(Float, nullable=False, default=0.0)
    validity_days = Column(Integer, nullable=False)
    data_limit_gb = Column(Integer, nullable=False)
    server_id = Column(Integer, ForeignKey("servers.id"))
    rate_limit = Column(String(50), nullable=True) # e.g. 8M/8M
    is_active = Column(Boolean, default=True)
    
    server = relationship("Server", back_populates="profiles")
    subscriptions = relationship("Subscription", back_populates="profile")

class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    server_id = Column(Integer, ForeignKey("servers.id"))
    profile_id = Column(Integer, ForeignKey("profiles.id"))
    
    mikrotik_username = Column(String, unique=True, nullable=False)
    mikrotik_password = Column(String, nullable=False)
    
    status = Column(String, default="active")  # active, expired, banned
    start_date = Column(DateTime(timezone=True), server_default=func.now())
    expiry_date = Column(DateTime(timezone=True), nullable=False)
    
    total_limit_bytes = Column(BigInteger, default=0)
    used_bytes = Column(BigInteger, default=0)
    
    deletion_warning_sent_at = Column(DateTime(timezone=True), nullable=True)
    
    user = relationship("User", back_populates="subscriptions")
    server = relationship("Server", back_populates="subscriptions")
    profile = relationship("Profile", back_populates="subscriptions")

class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # deposit, purchase, refund, manual_adjustment
    description = Column(String, nullable=True)
    currency_unit = Column(String(10), default='USD')
    receipt_id = Column(Integer, ForeignKey("payment_receipts.id"), nullable=True)
    discount_code_id = Column(Integer, ForeignKey("discount_codes.id"), nullable=True)
    original_amount = Column(Float, nullable=True)
    discount_amount = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    user = relationship("User", back_populates="transactions")
    discount_code = relationship("DiscountCode", back_populates="transactions")

class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Float, nullable=False)
    receipt_file_id = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending, approved, rejected
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    admin_note = Column(String, nullable=True)
    unique_id = Column(String(20), nullable=True) # Short hash part
    user_caption = Column(String(500), nullable=True)
    receipt_type = Column(String(20), default="photo") # photo, document, text
    currency_unit = Column(String(10), default='USD')
    plan_id = Column(Integer, nullable=True) # For direct purchase flow
    is_wireguard = Column(Boolean, default=False)
    discount_code_id = Column(Integer, ForeignKey("discount_codes.id"), nullable=True)
    credit_amount = Column(Float, nullable=True)
    payable_amount = Column(Float, nullable=True)
    
    user = relationship("User", back_populates="receipts")
    discount_code = relationship("DiscountCode", back_populates="receipts")

class ReceiptNotification(Base):
    """Maps admin Telegram chat/message IDs for receipt approval UI (not users.id)."""

    __tablename__ = "receipt_notifications"

    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("payment_receipts.id"))
    admin_id = Column(BigInteger, nullable=False)  # Telegram chat id (can exceed int32)
    message_id = Column(BigInteger, nullable=False)

class AdminSetting(Base):
    __tablename__ = "admin_settings"
    
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True) 
    value_json = Column(JSON, nullable=True)
class OvpnConfig(Base):
    __tablename__ = "ovpn_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("servers.id"))
    config_content = Column(Text, nullable=False)
    filename = Column(String, nullable=False)
    display_name = Column(String, nullable=True) # e.g. "TCP Config"
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    server = relationship("Server", back_populates="ovpn_configs")

# Add relationship to Server
Server.ovpn_configs = relationship("OvpnConfig", back_populates="server", cascade="all, delete-orphan")

# Support Ticket System Models
class Ticket(Base):
    __tablename__ = "tickets"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    subject = Column(String(200), nullable=False)
    status = Column(String(20), default="open")  # open, closed, waiting_user, waiting_admin
    priority = Column(String(10), default="medium")  # low, medium, high
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)
    
    user = relationship("User", back_populates="tickets")
    messages = relationship("TicketMessage", back_populates="ticket", cascade="all, delete-orphan")

class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"))
    sender_type = Column(String(10), nullable=False)  # 'user' or 'admin'
    sender_id = Column(BigInteger, nullable=False)  # telegram_id
    message = Column(Text, nullable=False)
    attachment_file_id = Column(String(500), nullable=True)  # Telegram file_id for attachments
    attachment_type = Column(String(20), nullable=True)     # photo, video, document, audio, voice
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    ticket = relationship("Ticket", back_populates="messages")

# Add back_populates to User
User.tickets = relationship("Ticket", back_populates="user")

class Admin(Base):
    __tablename__ = "admins"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    added_by = Column(BigInteger, nullable=True) # ID of admin who added this one
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_super = Column(Boolean, default=False) # Only Super Admins can manage other admins
    permissions_json = Column(String, nullable=True)  # JSON list of permission keys; null = full access

# --- WireGuard Models ---

class WireGuardInterface(Base):
    __tablename__ = "wireguard_interfaces"
    
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("servers.id"))
    name = Column(String(50), nullable=False) # e.g. wg1, wg2
    public_key = Column(Text, nullable=False)
    private_key = Column(Text, nullable=False)
    address = Column(String(50), nullable=False, default="10.0.0.1/24")
    listen_port = Column(Integer, nullable=False)
    dns = Column(String(100), default="8.8.8.8, 1.1.1.1")
    endpoint_host = Column(String(100), nullable=True) # If None, use server.host
    mtu = Column(Integer, default=1420)
    keepalive = Column(Integer, default=25)
    max_users = Column(Integer, default=20)
    current_users = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    
    # Advanced Networking
    upstream_interface = Column(String(50), nullable=True)
    routing_mark = Column(String(50), nullable=True)  # Mangle mark-routing (assign)
    nat_routing_mark = Column(String(50), nullable=True)  # NAT rule routing-mark (match)
    nat_dst_address = Column(String(50), nullable=True, default="127.0.0.1")
    nat_dst_address_list = Column(String(80), nullable=True)  # NAT dst-address-list match
    nat_dst_negate = Column(Boolean, default=True, nullable=False)  # MikroTik ! prefix on dst match
    gateway = Column(String(50), nullable=True)
    route_table = Column(String(50), nullable=True)  # /ip/route routing-table (falls back to routing_mark)
    route_dst_address = Column(String(50), nullable=True, default="0.0.0.0/0")
    route_distance = Column(Integer, default=1, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    server = relationship("Server", back_populates="wireguard_interfaces")
    users = relationship("WireGuardSubscription", back_populates="interface")

class WireGuardProfile(Base):
    __tablename__ = "wireguard_profiles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    volume_gb = Column(Integer, nullable=True) # volume in GB
    duration_days = Column(Integer, nullable=False)
    price_rial = Column(BigInteger, default=0)
    price_toman = Column(BigInteger, default=0)
    price_usd = Column(Float, default=0.0)
    rate_limit = Column(String(50), nullable=True) # e.g. 8M/8M
    is_active = Column(Boolean, default=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=True) # Optional per-server profile
    
    subscriptions = relationship("WireGuardSubscription", back_populates="profile")

class WireGuardSubscription(Base):
    __tablename__ = "wireguard_subscriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    interface_id = Column(Integer, ForeignKey("wireguard_interfaces.id"))
    profile_id = Column(Integer, ForeignKey("wireguard_profiles.id"))
    
    unique_identifier = Column(String(100), unique=True, index=True) # Comment ID in MikroTik
    peer_public_key = Column(Text, nullable=False)
    peer_private_key = Column(Text, nullable=False)
    assigned_ip = Column(String(20)) # e.g. 10.0.0.2
    comment_text = Column(Text)
    
    total_bytes_rx = Column(BigInteger, default=0)
    total_bytes_tx = Column(BigInteger, default=0)
    bytes_remaining = Column(BigInteger)
    
    # Cumulative tracking helpers: store last seen router values
    last_router_rx = Column(BigInteger, default=0)
    last_router_tx = Column(BigInteger, default=0)
    
    status = Column(String(20), default="pending") # active, expired, disabled, pending
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expiry_date = Column(DateTime(timezone=True))
    last_connected_at = Column(DateTime(timezone=True))
    deletion_warning_sent_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="wireguard_subscriptions")
    interface = relationship("WireGuardInterface", back_populates="users")
    profile = relationship("WireGuardProfile", back_populates="subscriptions")

# Add new relationships to existing models
Server.wireguard_interfaces = relationship("WireGuardInterface", back_populates="server", cascade="all, delete-orphan")
User.wireguard_subscriptions = relationship("WireGuardSubscription", back_populates="user", cascade="all, delete-orphan")


class DiscountCode(Base):
    __tablename__ = "discount_codes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    code = Column(String(64), unique=True, index=True, nullable=False)
    discount_type = Column(String(20), nullable=False)  # percent | fixed
    value = Column(Float, nullable=False)
    currency_unit = Column(String(10), nullable=True)  # USD | TOMAN | RIAL (for fixed)
    max_uses_per_user = Column(Integer, nullable=False, default=1)
    max_total_uses = Column(Integer, nullable=True)
    total_uses = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    redemptions = relationship("DiscountRedemption", back_populates="discount_code")
    transactions = relationship("Transaction", back_populates="discount_code")
    receipts = relationship("PaymentReceipt", back_populates="discount_code")


class DiscountRedemption(Base):
    __tablename__ = "discount_redemptions"

    id = Column(Integer, primary_key=True, index=True)
    discount_code_id = Column(Integer, ForeignKey("discount_codes.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    context = Column(String(30), nullable=False)
    original_amount = Column(Float, nullable=False)
    discount_amount = Column(Float, nullable=False)
    final_amount = Column(Float, nullable=False)
    currency_unit = Column(String(10), nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    receipt_id = Column(Integer, ForeignKey("payment_receipts.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    discount_code = relationship("DiscountCode", back_populates="redemptions")
    user = relationship("User", back_populates="discount_redemptions")


User.discount_redemptions = relationship("DiscountRedemption", back_populates="user")


class AdminAuditLog(Base):
    """Durable record of sensitive admin actions for accountability and forensics."""

    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_telegram_id = Column(BigInteger, nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(32), nullable=True)   # e.g. "user", "subscription", "server"
    target_id = Column(String(64), nullable=True)     # DB or Telegram ID as string
    detail = Column(Text, nullable=True)              # JSON blob with extra context
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
