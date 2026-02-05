# 🛰️ VPN Management Bot (Telegram + MikroTik)

<div align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" />
  <img src="https://img.shields.io/badge/MikroTik-RouterOS-EE3A3E?style=for-the-badge&logo=mikrotik&logoColor=white" />
  <img src="https://img.shields.io/badge/Database-SQLAlchemy%20(Async)-D71F00?style=for-the-badge&logo=sqlite&logoColor=white" />
  <br/>
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Production--Ready-YES-success?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Version-2.0.0-blue?style=for-the-badge" />
</div>

---

## 📖 Table of Contents / فهرست

### 🇬🇧 English Version
- [Introduction](#-introduction)
- [Key Features](#-key-features)
- [Architecture](#-architecture)
- [Installation](#-installation)
- [Configuration](#%EF%B8%8F-configuration)
- [MikroTik Setup](#-mikrotik-setup)
- [Bot Commands](#-bot-commands)
- [Admin Panel Guide](#-admin-panel-guide)
- [Customization](#-customization)
- [API Reference](#-api-reference)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)

### 🇮🇷 نسخه فارسی
- [معرفی](#-معرفی)
- [قابلیت‌های کلیدی](#-قابلیتهای-کلیدی)
- [نصب و راه‌اندازی](#-نصب-و-راهاندازی)
- [پیکربندی](#%EF%B8%8F-پیکربندی)
- [تنظیمات میکروتیک](#-تنظیمات-میکروتیک)
- [راهنمای پنل ادمین](#-راهنمای-پنل-ادمین)
- [شخصی‌سازی](#-شخصیسازی)
- [عیب‌یابی](#-عیبیابی)

---

# 🇬🇧 ENGLISH VERSION

## 🌟 Introduction

The **VPN Management Bot** is a production-ready, fully asynchronous Telegram bot for selling and managing VPN services. Built with Python 3.12+ and integrated directly with **MikroTik RouterOS User Manager v7**, it automates the entire VPN subscription lifecycle:

- ✅ User registration and profile management
- ✅ Wallet-based payment system with receipt verification
- ✅ Automatic VPN account provisioning on MikroTik
- ✅ Real-time subscription status and data usage tracking
- ✅ Multi-server support with per-server profiles
- ✅ Complete admin panel with user management tools
- ✅ Support ticket system for customer service
- ✅ Automated database backups every 6 hours

---

## 💎 Key Features

### 👤 User Features
| Feature | Description |
|---------|-------------|
| 🔐 **Easy Registration** | One-time registration with name and phone number |
| 💳 **Smart Wallet** | Deposit funds via card-to-card transfer with receipt photo |
| 🛒 **Buy Services** | Browse plans, purchase with wallet balance, instant activation |
| 📱 **My Subscriptions** | View active plans, data usage, expiry dates, download configs |
| 🎫 **Support Tickets** | Create tickets with text/photo, track status, receive replies |
| 📖 **Tutorials** | Access VPN setup guides and app download links |

### 🛡️ Admin Features
| Feature | Description |
|---------|-------------|
| 👤 **User Management** | Search users, reset passwords, add data, extend time, edit balance |
| 📋 **Receipt Approval** | Review pending receipts, approve/reject with notes |
| 🖥️ **Server Management** | Add/edit MikroTik servers, test connections |
| 📦 **Profile Management** | Create/edit service plans (price, data limit, validity) |
| 📢 **Notifications** | Broadcast messages to all users or targeted individuals |
| 🎫 **Ticket Management** | View/reply to support tickets, close resolved issues |
| 💾 **Backup System** | Manual/automatic database backups to Telegram |
| ⚙️ **Settings** | Payment cards, custom messages, wallet presets |

### 🔒 Security Features
- 🔐 AES-256 encryption for sensitive data (passwords)
- 🔑 Secret keyword access to admin panel
- 👑 Super Admin hierarchy (environment-based vs database admins)
- 🚫 Rate limiting for broadcasts (flood protection)

---

## 🏗️ Architecture

```
vpn_bot/
├── main.py                 # Application entry point
├── config.py               # Environment configuration
├── database.py             # Async SQLAlchemy setup
├── models.py               # Database models (User, Server, Subscription, etc.)
├── bot_handler.py          # User-facing bot handlers
├── admin_panel.py          # Admin panel handlers
├── admin_settings.py       # Admin settings management
├── admin_management.py     # Admin user management
├── admin_tickets.py        # Admin ticket handling
├── support_tickets.py      # User ticket system
├── mikrotik_manager.py     # MikroTik API integration
├── wallet_manager.py       # Wallet operations
├── backup_manager.py       # Database backup system
├── notification_manager.py # Broadcast notifications
├── utils.py                # Utility functions and logging
├── requirements.txt        # Python dependencies
├── install.sh              # Quick installation script
├── manage.sh               # Management CLI
└── tests/                  # Test suite
    ├── conftest.py
    ├── test_database_models.py
    ├── test_mikrotik_connection.py
    └── test_wallet_manager.py
```

---

## 📥 Installation

### Quick Install (Ubuntu/Debian)

```bash
bash <(curl -sL https://raw.githubusercontent.com/VeilVulp/vpn_bot/main/install.sh)
```

This will:
1. Install Python 3, Git, and dependencies
2. Clone the repository to `/opt/vpn_bot`
3. Create Python virtual environment
4. Guide you through configuration
5. Setup systemd service
6. Create global `vpnbot` command

### Manual Installation

```bash
# Clone repository
git clone https://github.com/VeilVulp/vpn_bot.git
cd vpn_bot

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env

# Run
python3 main.py
```

### Management Commands

After installation, use the management CLI:

```bash
sudo vpnbot              # Open management menu
# Or directly:
sudo ./manage.sh         # From project directory
```

Management menu options:
- Start/Stop/Restart bot
- View live logs
- Edit configuration
- Update bot (git pull)
- Backup/Restore database
- Uninstall

---

## ⚙️ Configuration

### Environment Variables (.env)

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather | `123456:ABC-DEF...` |
| `ADMIN_IDS` | ✅ | Comma-separated Super Admin Telegram IDs | `123456789,987654321` |
| `DATABASE_URL` | ❌ | SQLAlchemy database URL | `sqlite+aiosqlite:///vpn_bot.db` |
| `MIKROTIK_HOST` | ❌ | Default MikroTik host (IP or domain) | `192.168.88.1` |
| `MIKROTIK_USERNAME` | ❌ | Default MikroTik username | `admin` |
| `MIKROTIK_PASSWORD` | ❌ | Default MikroTik password | `yourpassword` |
| `MIKROTIK_PORT` | ❌ | MikroTik API port | `8728` (default) |
| `BACKUP_GROUP_ID` | ❌ | Telegram chat ID for auto-backups | `-1001234567890` |
| `ENCRYPTION_KEY` | ✅ | Fernet encryption key | Auto-generated |
| `DEBUG` | ❌ | Enable debug mode | `False` |

### MikroTik Connection Format

The bot supports **IP address** or **domain name** for MikroTik connections:

```env
# IP Address
MIKROTIK_HOST=192.168.88.1
MIKROTIK_PORT=8728

# Domain Name
MIKROTIK_HOST=router.example.com
MIKROTIK_PORT=8728

# Non-standard port
MIKROTIK_HOST=vpn.myserver.com
MIKROTIK_PORT=8729
```

> **Note:** Each server added via the admin panel has its own `host` and `port` fields in the database.

---

## 🖧 MikroTik Setup

### User Manager v7 Requirements

1. **Enable User Manager Package:**
   ```
   /system/package/enable userman
   /system/reboot
   ```

2. **Create API User:**
   ```
   /user/add name=vpnbot password=SecurePassword123 group=full
   ```

3. **Enable API Service:**
   ```
   /ip/service/enable api
   /ip/service/set api port=8728
   ```

4. **Create Default Profile (optional):**
   ```
   /user-manager/profile/add name=default
   ```

### Firewall Considerations

If your router has strict firewall rules, allow API access:

```
/ip/firewall/filter/add chain=input protocol=tcp dst-port=8728 action=accept comment="Allow API"
```

### Supported Operations

The bot performs these MikroTik operations via API:
- Create/delete User Manager users
- Assign profiles to users
- Set data limits (limitations)
- Reset passwords
- Enable/disable users
- Query active sessions
- Get usage statistics

---

## 🤖 Bot Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/start` | Open main menu |
| `/help` | Show help message |
| `/cancel` | Cancel current operation |

### Menu Navigation

```
📱 Main Menu
├── 💳 My Wallet → View balance, top-up, transaction history
├── 🛒 Buy Service → Browse plans, purchase subscriptions
├── 📱 My Subscriptions → View active plans, download configs
├── 🎫 Support → Create/view tickets, contact admins
├── 📖 Tutorial → VPN setup guides
└── 📥 Download Apps → Get VPN client apps
```

---

## 🛠️ Admin Panel Guide

### Accessing Admin Panel

1. **Via Secret Keyword:** Type the secret keyword in chat (default: `AdminPanel`)
2. **Via Bot Menu:** Super Admins see admin options in main menu

### Admin Menu Structure

```
🛡️ Admin Panel
├── 🔍 Search User → Find users by Telegram ID/username
├── 📋 Pending Receipts → Approve/reject payment receipts
├── 🖥️ Servers → Manage MikroTik servers
├── 📦 Profiles → Manage service plans
├── 🎫 Tickets → View/reply to support tickets
├── 📢 Notifications → Broadcast messages
├── ⚙️ Settings → Payment cards, messages, presets
└── 💾 Backup → Manual database backup
```

### User Management Actions

When searching for a user, admins can:
- 🔄 **Reset Password** - Generate new MikroTik password
- ➕ **Add Data** - Increase data limit (GB)
- ⏰ **Extend Time** - Add days to subscription
- 💰 **Edit Balance** - Adjust wallet balance
- 🚫 **Disable User** - Deactivate subscription
- 🗑️ **Delete User** - Remove from MikroTik

---

## 🎨 Customization

### Custom Messages

All bot messages can be customized via Admin Panel → Settings → Custom Messages:

| Message Key | Purpose |
|-------------|---------|
| `welcome_message` | Message shown on `/start` |
| `buy_service_text` | Instructions in purchase flow |
| `wallet_info_text` | Wallet top-up instructions |
| `empty_wallet_text` | "No balance" message |
| `subscription_expired_text` | Expiry notification |
| `payment_approved_text` | Payment approval message |
| `payment_rejected_text` | Payment rejection message |
| `support_hours_text` | Support availability info |
| `tutorial_text` | VPN setup instructions |
| `download_apps_text` | App download links |
| `admin_secret_keyword` | Secret word to open admin panel |

### Wallet Presets

Configure quick top-up amounts via Admin Panel → Settings → Wallet Presets:
- Add common amounts (e.g., $5, $10, $20, $50)
- Users see buttons for quick selection

### Payment Cards

Add bank card information for receiving payments:
- Card Number
- Card Holder Name
- Bank Name

Users see this when uploading payment receipts.

### Connection Info

Per-server VPN connection details:
- L2TP Server IP
- L2TP Secret (PSK)
- SSTP Server IP

---

## 📚 API Reference

### MikroTikManager Class

```python
from mikrotik_manager import MikroTikManager

# Initialize with custom credentials
manager = MikroTikManager(
    host="192.168.88.1",  # or domain name
    username="admin",
    password="password",
    port=8728
)

# Connect
manager.connect()

# Operations
manager.create_user("username", "password", "profile_name")
manager.get_user_info("username")
manager.reset_password("username", "new_password")
manager.disable_user("username")
manager.enable_user("username")
manager.delete_user("username")

# Close connection
manager.close()
```

### WalletManager Class

```python
from wallet_manager import WalletManager

# Deposit funds
await WalletManager.deposit(user_id, amount, "Description")

# Deduct funds
success = await WalletManager.deduct(user_id, amount, "Purchase")

# Receipt operations
await WalletManager.approve_receipt(receipt_id, admin_id)
await WalletManager.reject_receipt(receipt_id, admin_id, reason)
```

### Database Models

| Model | Description |
|-------|-------------|
| `User` | Telegram users with wallet balance |
| `Server` | MikroTik server configurations |
| `Profile` | Service plans (price, data, validity) |
| `Subscription` | User subscriptions linking users to profiles |
| `Transaction` | Wallet transaction history |
| `PaymentReceipt` | Payment receipt submissions |
| `Ticket` | Support tickets |
| `TicketMessage` | Ticket conversation messages |
| `Admin` | Database-added admins |
| `AdminSetting` | Key-value settings storage |
| `OvpnConfig` | OVPN configuration files |

---

## 🔧 Troubleshooting

### Common Issues

**Bot not responding:**
```bash
sudo systemctl status vpn_bot
sudo journalctl -u vpn_bot -f
```

**MikroTik connection failed:**
- Verify API service is enabled
- Check firewall rules
- Confirm credentials
- Try: `MIKROTIK_PORT=8728` (not 8729 for SSL)

**Database errors:**
```bash
# Backup and reset
cp vpn_bot.db vpn_bot.db.bak
rm vpn_bot.db
sudo systemctl restart vpn_bot
```

**Permission denied:**
```bash
sudo chown -R root:root /opt/vpn_bot
sudo chmod +x /opt/vpn_bot/manage.sh
```

### Logs Location

```bash
# Live logs
journalctl -u vpn_bot -f

# Full log history
journalctl -u vpn_bot --since "1 hour ago"
```

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

# 🇮🇷 نسخه فارسی

## 🌟 معرفی

**ربات مدیریت VPN** یک راه‌حل آماده برای تولید و کاملاً ناهمزمان (Asynchronous) برای فروش و مدیریت سرویس‌های VPN در بستر تلگرام است. این ربات با **Python 3.12+** نوشته شده و مستقیماً با **User Manager v7 میکروتیک** یکپارچه شده است.

### چرا این ربات؟
- ✅ نصب آسان با یک دستور
- ✅ پنل ادمین کامل و حرفه‌ای
- ✅ سیستم کیف پول با تایید رسید
- ✅ ایجاد خودکار اکانت روی میکروتیک
- ✅ پشتیبانی از چندین سرور
- ✅ سیستم تیکت پشتیبانی
- ✅ بک‌آپ خودکار هر ۶ ساعت

---

## 💎 قابلیت‌های کلیدی

### 👤 امکانات کاربر

| قابلیت | توضیحات |
|--------|---------|
| 🔐 **ثبت‌نام آسان** | ثبت نام یکباره با نام و شماره تلفن |
| 💳 **کیف پول هوشمند** | شارژ با ارسال رسید کارت به کارت |
| 🛒 **خرید سرویس** | مشاهده پلن‌ها، خرید با موجودی کیف پول |
| 📱 **اشتراک‌های من** | مشاهده پلن‌های فعال، مصرف دیتا، تاریخ انقضا |
| 🎫 **تیکت پشتیبانی** | ایجاد تیکت با متن/عکس، پیگیری وضعیت |
| 📖 **آموزش** | دسترسی به راهنمای تنظیم VPN |

### 🛡️ امکانات ادمین

| قابلیت | توضیحات |
|--------|---------|
| 👤 **مدیریت کاربران** | جستجو، ریست رمز، افزایش دیتا، تمدید زمان |
| 📋 **تایید رسیدها** | بررسی رسیدهای پرداخت، تایید/رد |
| 🖥️ **مدیریت سرورها** | افزودن/ویرایش سرورهای میکروتیک |
| 📦 **مدیریت پلن‌ها** | ایجاد پلن‌های سرویس (قیمت، حجم، مدت) |
| 📢 **اطلاع‌رسانی** | ارسال پیام به همه کاربران |
| 🎫 **مدیریت تیکت‌ها** | مشاهده و پاسخ به تیکت‌ها |
| 💾 **بک‌آپ** | پشتیبان‌گیری دستی/خودکار |
| ⚙️ **تنظیمات** | کارت‌های پرداخت، پیام‌های سفارشی |

---

## 📥 نصب و راه‌اندازی

### نصب سریع (اوبونتو/دبیان)

```bash
bash <(curl -sL https://raw.githubusercontent.com/VeilVulp/vpn_bot/main/install.sh)
```

این دستور:
1. پایتون ۳ و وابستگی‌ها را نصب می‌کند
2. مخزن را در `/opt/vpn_bot` کلون می‌کند
3. محیط مجازی پایتون ایجاد می‌کند
4. شما را در پیکربندی راهنمایی می‌کند
5. سرویس systemd تنظیم می‌کند
6. دستور جهانی `vpnbot` ایجاد می‌کند

### نصب دستی

```bash
# کلون مخزن
git clone https://github.com/VeilVulp/vpn_bot.git
cd vpn_bot

# ایجاد محیط مجازی
python3 -m venv .venv
source .venv/bin/activate

# نصب وابستگی‌ها
pip install -r requirements.txt

# پیکربندی
cp .env.example .env
nano .env

# اجرا
python3 main.py
```

### دستورات مدیریت

پس از نصب:

```bash
sudo vpnbot              # باز کردن منوی مدیریت
# یا مستقیماً:
sudo ./manage.sh         # از دایرکتوری پروژه
```

گزینه‌های منوی مدیریت:
- شروع/توقف/ریستارت ربات
- مشاهده لاگ زنده
- ویرایش پیکربندی
- بروزرسانی ربات (git pull)
- پشتیبان‌گیری/بازیابی دیتابیس
- حذف نصب

---

## ⚙️ پیکربندی

### متغیرهای محیطی (.env)

| متغیر | الزامی | توضیحات | مثال |
|-------|--------|---------|------|
| `BOT_TOKEN` | ✅ | توکن ربات تلگرام از @BotFather | `123456:ABC-DEF...` |
| `ADMIN_IDS` | ✅ | آیدی ادمین‌های اصلی (با کاما جدا شده) | `123456789,987654321` |
| `DATABASE_URL` | ❌ | آدرس دیتابیس | `sqlite+aiosqlite:///vpn_bot.db` |
| `MIKROTIK_HOST` | ❌ | آدرس میکروتیک پیش‌فرض (IP یا دامنه) | `192.168.88.1` |
| `MIKROTIK_USERNAME` | ❌ | نام کاربری میکروتیک | `admin` |
| `MIKROTIK_PASSWORD` | ❌ | رمز عبور میکروتیک | `yourpassword` |
| `MIKROTIK_PORT` | ❌ | پورت API میکروتیک | `8728` |
| `BACKUP_GROUP_ID` | ❌ | آیدی گروه/کانال برای بک‌آپ خودکار | `-1001234567890` |
| `ENCRYPTION_KEY` | ✅ | کلید رمزنگاری | خودکار تولید می‌شود |

### فرمت اتصال میکروتیک

ربات از **آدرس IP** یا **نام دامنه** پشتیبانی می‌کند:

```env
# آدرس IP
MIKROTIK_HOST=192.168.88.1
MIKROTIK_PORT=8728

# نام دامنه
MIKROTIK_HOST=router.example.com
MIKROTIK_PORT=8728

# پورت غیر استاندارد
MIKROTIK_HOST=vpn.myserver.com
MIKROTIK_PORT=8729
```

> **توجه:** هر سرور اضافه شده از پنل ادمین، فیلدهای `host` و `port` جداگانه دارد.

---

## 🖧 تنظیمات میکروتیک

### الزامات User Manager v7

1. **فعال‌سازی User Manager:**
   ```
   /system/package/enable userman
   /system/reboot
   ```

2. **ایجاد کاربر API:**
   ```
   /user/add name=vpnbot password=SecurePassword123 group=full
   ```

3. **فعال‌سازی سرویس API:**
   ```
   /ip/service/enable api
   /ip/service/set api port=8728
   ```

4. **ایجاد پروفایل پیش‌فرض (اختیاری):**
   ```
   /user-manager/profile/add name=default
   ```

### فایروال

اگر قوانین فایروال سخت‌گیرانه دارید:

```
/ip/firewall/filter/add chain=input protocol=tcp dst-port=8728 action=accept comment="Allow API"
```

---

## 🛠️ راهنمای پنل ادمین

### دسترسی به پنل ادمین

1. **با کلمه رمز:** کلمه رمز را در چت تایپ کنید (پیش‌فرض: `AdminPanel`)
2. **از منوی ربات:** سوپر ادمین‌ها گزینه‌های ادمین را در منوی اصلی می‌بینند

### ساختار منوی ادمین

```
🛡️ پنل ادمین
├── 🔍 جستجوی کاربر → یافتن کاربران با آیدی/نام کاربری
├── 📋 رسیدهای در انتظار → تایید/رد رسیدهای پرداخت
├── 🖥️ سرورها → مدیریت سرورهای میکروتیک
├── 📦 پلن‌ها → مدیریت پلن‌های سرویس
├── 🎫 تیکت‌ها → مشاهده/پاسخ به تیکت‌های پشتیبانی
├── 📢 اطلاع‌رسانی → ارسال پیام همگانی
├── ⚙️ تنظیمات → کارت‌های پرداخت، پیام‌ها، پیش‌تنظیم‌ها
└── 💾 پشتیبان‌گیری → بک‌آپ دستی دیتابیس
```

### عملیات مدیریت کاربر

هنگام جستجوی کاربر، ادمین می‌تواند:
- 🔄 **ریست رمز** - تولید رمز جدید میکروتیک
- ➕ **افزایش دیتا** - افزودن حجم (گیگابایت)
- ⏰ **تمدید زمان** - افزودن روز به اشتراک
- 💰 **ویرایش موجودی** - تغییر موجودی کیف پول
- 🚫 **غیرفعال‌سازی** - غیرفعال کردن اشتراک
- 🗑️ **حذف کاربر** - حذف از میکروتیک

---

## 🎨 شخصی‌سازی

### پیام‌های سفارشی

تمام پیام‌های ربات قابل سفارشی‌سازی هستند:

**پنل ادمین → تنظیمات → پیام‌های سفارشی**

| کلید پیام | کاربرد |
|-----------|--------|
| `welcome_message` | پیام خوش‌آمدگید |
| `buy_service_text` | راهنمای خرید |
| `wallet_info_text` | راهنمای شارژ کیف پول |
| `payment_approved_text` | پیام تایید پرداخت |
| `payment_rejected_text` | پیام رد پرداخت |
| `support_hours_text` | ساعات پشتیبانی |
| `tutorial_text` | راهنمای تنظیم VPN |
| `admin_secret_keyword` | کلمه رمز ورود به پنل |

### پیش‌تنظیم‌های کیف پول

مبالغ شارژ سریع را پیکربندی کنید:

**پنل ادمین → تنظیمات → پیش‌تنظیم‌های کیف پول**
- افزودن مبالغ رایج (مثلاً ۵۰، ۱۰۰، ۲۰۰ هزار تومان)
- کاربران دکمه‌های انتخاب سریع می‌بینند

### کارت‌های پرداخت

اطلاعات کارت بانکی برای دریافت پرداخت:
- شماره کارت
- نام صاحب کارت
- نام بانک

کاربران هنگام آپلود رسید این اطلاعات را می‌بینند.

---

## 🔧 عیب‌یابی

### مشکلات رایج

**ربات پاسخ نمی‌دهد:**
```bash
sudo systemctl status vpn_bot
sudo journalctl -u vpn_bot -f
```

**اتصال میکروتیک ناموفق:**
- سرویس API فعال باشد
- قوانین فایروال بررسی شود
- اطلاعات ورود صحیح باشد
- پورت صحیح: `8728` (نه `8729`)

**خطای دیتابیس:**
```bash
# پشتیبان‌گیری و ریست
cp vpn_bot.db vpn_bot.db.bak
rm vpn_bot.db
sudo systemctl restart vpn_bot
```

### محل لاگ‌ها

```bash
# لاگ زنده
journalctl -u vpn_bot -f

# تاریخچه کامل
journalctl -u vpn_bot --since "1 hour ago"
```

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.

## 📞 Support

For support, join our Telegram community or open an issue on GitHub.

---

<div align="center">

Developed with ❤️ by [VeilVulp](https://github.com/VeilVulp)

**⭐ Star this repository if you find it useful! ⭐**

</div>