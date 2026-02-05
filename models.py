from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, BigInteger, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import datetime
from cryptography.fernet import Fernet
import os

# Encryption Setup
KEY = os.getenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
cipher = Fernet(KEY.encode())

def encrypt_text(text: str) -> str:
    if not text: return ""
    return cipher.encrypt(text.encode()).decode()

def decrypt_text(text: str) -> str:
    if not text: return ""
    try:
        return cipher.decrypt(text.encode()).decode()
    except:
        return text # Fallback to plaintext if decryption fails (for migration)

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
    price = Column(Float, nullable=False)
    validity_days = Column(Integer, nullable=False)
    data_limit_gb = Column(Integer, nullable=False)
    server_id = Column(Integer, ForeignKey("servers.id"))
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    user = relationship("User", back_populates="transactions")

class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Float, nullable=False)
    receipt_file_id = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending, approved, rejected
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    admin_note = Column(String, nullable=True)
    
    user = relationship("User", back_populates="receipts")

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
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    server = relationship("Server", back_populates="ovpn_config")

# Add back_populates to Server
Server.ovpn_config = relationship("OvpnConfig", uselist=False, back_populates="server")

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
