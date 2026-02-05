#!/bin/bash

# =====================================================
# 🚀 VPN Telegram Bot - Smart Management CLI
# Repository: https://github.com/VeilVulp/vpn_bot
# =====================================================
# Full-featured management interface for Ubuntu servers
# =====================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
WHITE='\033[1;37m'
NC='\033[0m'

# Configuration
BOT_DIR=$(dirname "$(readlink -f "$0")")
SERVICE_NAME="vpn_bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${BOT_DIR}/.env"
VENV_DIR="${BOT_DIR}/.venv"

# =====================================================
# UTILITY FUNCTIONS
# =====================================================

show_header() {
    clear
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}          🛰️  VPN TELEGRAM BOT MANAGEMENT${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    # Show status
    if systemctl is-active --quiet $SERVICE_NAME 2>/dev/null; then
        echo -e "${GREEN}    ● Bot Status: RUNNING${NC}"
    else
        echo -e "${RED}    ○ Bot Status: STOPPED${NC}"
    fi
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}❌ Please run this script with sudo or as root!${NC}"
        exit 1
    fi
}

pause() {
    echo ""
    read -p "Press Enter to continue..."
}

# =====================================================
# INSTALLATION
# =====================================================

install_dependencies() {
    echo -e "${BLUE}📦 Installing system dependencies...${NC}"
    apt update -y > /dev/null 2>&1
    apt install -y python3 python3-pip python3-venv git curl nano > /dev/null 2>&1
    echo -e "${GREEN}✅ Dependencies installed.${NC}"
}

setup_virtualenv() {
    echo -e "${BLUE}🐍 Setting up Python virtual environment...${NC}"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip > /dev/null 2>&1
    pip install -r "$BOT_DIR/requirements.txt" > /dev/null 2>&1
    echo -e "${GREEN}✅ Virtual environment ready.${NC}"
}

configure_env() {
    echo -e "${BLUE}⚙️  Bot Configuration${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    # Get BOT_TOKEN
    while true; do
        echo -e "${YELLOW}Enter your Telegram Bot Token:${NC}"
        echo -e "${CYAN}(Get from @BotFather on Telegram)${NC}"
        read -p "🔑 Token: " BOT_TOKEN
        
        if [ -z "$BOT_TOKEN" ]; then
            echo -e "${RED}❌ Token cannot be empty!${NC}"
            continue
        fi
        break
    done
    
    # Get ADMIN_IDS
    echo ""
    echo -e "${YELLOW}Enter Admin Telegram IDs (comma-separated):${NC}"
    echo -e "${CYAN}(e.g., 123456789,987654321)${NC}"
    read -p "👤 Admin IDs: " ADMIN_IDS
    
    # Get BACKUP_GROUP_ID (optional)
    echo ""
    echo -e "${YELLOW}Enter Backup Group/Channel ID (optional):${NC}"
    echo -e "${CYAN}(Leave empty to skip auto-backup)${NC}"
    read -p "💾 Backup ID: " BACKUP_GROUP_ID
    
    # Generate encryption key
    ENCRYPTION_KEY=$(openssl rand -base64 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    
    # Confirm
    echo ""
    echo -e "${YELLOW}━━━ Configuration Summary ━━━${NC}"
    echo -e "Token: ${CYAN}${BOT_TOKEN:0:10}...${NC}"
    echo -e "Admins: ${CYAN}${ADMIN_IDS}${NC}"
    echo -e "Backup ID: ${CYAN}${BACKUP_GROUP_ID:-Not set}${NC}"
    echo ""
    read -p "Is this correct? (y/n): " CONFIRM
    
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        configure_env
        return
    fi
    
    # Write .env file
    cat > "$ENV_FILE" << EOF
# =====================================================
# VPN Telegram Bot Configuration
# Generated: $(date)
# =====================================================

# --- BOT SETTINGS ---
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}

# --- DATABASE ---
DATABASE_URL=sqlite+aiosqlite:///vpn_bot.db

# --- MIKROTIK DEFAULTS (Optional - servers are managed via bot) ---
MIKROTIK_HOST=
MIKROTIK_USERNAME=
MIKROTIK_PASSWORD=
MIKROTIK_PORT=8728

# --- BACKUP ---
BACKUP_GROUP_ID=${BACKUP_GROUP_ID}

# --- SECURITY ---
ENCRYPTION_KEY=${ENCRYPTION_KEY}

# --- DEBUG ---
DEBUG=False
EOF
    
    chmod 600 "$ENV_FILE"
    echo -e "${GREEN}✅ Configuration saved to .env${NC}"
}

setup_systemd() {
    echo -e "${BLUE}⚙️  Setting up systemd service...${NC}"
    
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=VPN Telegram Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${BOT_DIR}
Environment="PATH=${VENV_DIR}/bin"
ExecStart=${VENV_DIR}/bin/python3 ${BOT_DIR}/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME > /dev/null 2>&1
    echo -e "${GREEN}✅ Systemd service configured.${NC}"
}

easy_install() {
    show_header
    echo -e "${MAGENTA}🛠  EASY INSTALLER${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    # Step 1: Dependencies
    echo -e "${BLUE}[1/4] System Dependencies${NC}"
    install_dependencies
    echo ""
    
    # Step 2: Virtual Environment
    echo -e "${BLUE}[2/4] Python Environment${NC}"
    setup_virtualenv
    echo ""
    
    # Step 3: Configuration
    echo -e "${BLUE}[3/4] Bot Configuration${NC}"
    configure_env
    echo ""
    
    # Step 4: Systemd
    echo -e "${BLUE}[4/4] Service Setup${NC}"
    setup_systemd
    echo ""
    
    # Create global command
    cat > /usr/local/bin/vpnbot << 'EOF2'
#!/bin/bash
cd /opt/vpn_bot 2>/dev/null || cd "$(dirname "$0")"
sudo ./manage.sh
EOF2
    chmod +x /usr/local/bin/vpnbot 2>/dev/null || true
    
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}      ✨ INSTALLATION COMPLETE ✨${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${YELLOW}📌 Quick Commands:${NC}"
    echo -e "   ${CYAN}vpnbot${NC}           - Open this management menu"
    echo -e "   ${CYAN}systemctl status vpn_bot${NC} - Check service status"
    echo ""
    
    read -p "🚀 Start the bot now? (y/n): " START_NOW
    if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
        systemctl start $SERVICE_NAME
        echo -e "${GREEN}✅ Bot started!${NC}"
        sleep 2
    fi
    
    main_menu
}

# =====================================================
# SERVICE MANAGEMENT
# =====================================================

start_bot() {
    echo -e "${BLUE}▶️  Starting bot...${NC}"
    systemctl start $SERVICE_NAME
    sleep 2
    if systemctl is-active --quiet $SERVICE_NAME; then
        echo -e "${GREEN}✅ Bot started successfully!${NC}"
    else
        echo -e "${RED}❌ Failed to start. Check logs for errors.${NC}"
    fi
    pause
}

stop_bot() {
    echo -e "${YELLOW}⏹  Stopping bot...${NC}"
    systemctl stop $SERVICE_NAME
    echo -e "${GREEN}✅ Bot stopped.${NC}"
    pause
}

restart_bot() {
    echo -e "${BLUE}🔄 Restarting bot...${NC}"
    systemctl restart $SERVICE_NAME
    sleep 2
    if systemctl is-active --quiet $SERVICE_NAME; then
        echo -e "${GREEN}✅ Bot restarted successfully!${NC}"
    else
        echo -e "${RED}❌ Failed to restart. Check logs for errors.${NC}"
    fi
    pause
}

view_logs() {
    echo -e "${YELLOW}📜 Live Logs (Press Ctrl+C to exit):${NC}"
    echo ""
    journalctl -u $SERVICE_NAME -f --no-pager
}

view_status() {
    echo -e "${BLUE}📊 Service Status:${NC}"
    echo ""
    systemctl status $SERVICE_NAME --no-pager
    pause
}

edit_config() {
    echo -e "${BLUE}✏️  Opening configuration file...${NC}"
    nano "$ENV_FILE"
    
    echo ""
    read -p "Restart bot to apply changes? (y/n): " RESTART
    if [[ "$RESTART" =~ ^[Yy]$ ]]; then
        restart_bot
    fi
}

# =====================================================
# ADVANCED OPTIONS
# =====================================================

rollback_update() {
    BACKUP_DIR="$1"
    
    echo -e "${YELLOW}🔄 Rolling back update...${NC}"
    
    # Restore database
    if [ -f "$BACKUP_DIR/vpn_bot.db" ]; then
        cp "$BACKUP_DIR/vpn_bot.db" "$BOT_DIR/"
        echo -e "${GREEN}✅ Database restored${NC}"
    fi
    
    # Revert code
    if [ -f "$BACKUP_DIR/prev_commit.txt" ]; then
        PREV_COMMIT=$(cat "$BACKUP_DIR/prev_commit.txt")
        cd "$BOT_DIR"
        git reset --hard "$PREV_COMMIT" > /dev/null 2>&1
        echo -e "${GREEN}✅ Code reverted to ${PREV_COMMIT:0:8}${NC}"
    fi
    
    # Restart with old version
    systemctl start $SERVICE_NAME 2>/dev/null || true
    
    echo -e "${YELLOW}⚠️  Update rolled back. Please check logs with: journalctl -u vpn_bot -n 50${NC}"
    pause
}

update_bot() {
    show_header
    echo -e "${BLUE}🔄 Multi-Phase Update System${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    cd "$BOT_DIR"
    
    # ═══════════════════════════════════════════════════════
    # Phase 1: Pre-Update
    # ═══════════════════════════════════════════════════════
    echo -e "${YELLOW}[Phase 1/5] Pre-Update Checks${NC}"
    
    # Stop service gracefully
    if systemctl is-active --quiet $SERVICE_NAME 2>/dev/null; then
        systemctl stop $SERVICE_NAME
        echo -e "${GREEN}  ✅ Service stopped${NC}"
    else
        echo -e "${YELLOW}  ⚠️  Service was not running${NC}"
    fi
    
    # Create backup directory
    BACKUP_TIME=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="$BOT_DIR/backups/update_$BACKUP_TIME"
    mkdir -p "$BACKUP_DIR"
    
    # Backup database
    if [ -f "$BOT_DIR/vpn_bot.db" ]; then
        cp "$BOT_DIR/vpn_bot.db" "$BACKUP_DIR/"
        echo -e "${GREEN}  ✅ Database backed up${NC}"
    fi
    
    # Backup .env
    if [ -f "$ENV_FILE" ]; then
        cp "$ENV_FILE" "$BACKUP_DIR/"
        echo -e "${GREEN}  ✅ Configuration backed up${NC}"
    fi
    
    # Save current commit for rollback
    PREV_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    echo "$PREV_COMMIT" > "$BACKUP_DIR/prev_commit.txt"
    echo -e "${GREEN}  ✅ Commit saved: ${PREV_COMMIT:0:8}${NC}"
    
    # ═══════════════════════════════════════════════════════
    # Phase 2: Code Update
    # ═══════════════════════════════════════════════════════
    echo ""
    echo -e "${YELLOW}[Phase 2/5] Pulling Latest Code${NC}"
    
    # Stash any local changes (just in case)
    git stash > /dev/null 2>&1 || true
    
    # Use fetch + reset --hard to handle forced pushes (rewritten history)
    echo -e "  Fetching updates..."
    if ! git fetch origin main; then
        echo -e "${RED}  ❌ Git fetch failed! Check internet/permissions.${NC}"
        rollback_update "$BACKUP_DIR"
        return 1
    fi

    echo -e "  Applying updates (Force Reset)..."
    if ! git reset --hard origin/main; then
        echo -e "${RED}  ❌ Git reset failed!${NC}"
        rollback_update "$BACKUP_DIR"
        return 1
    fi
    
    echo -e "${GREEN}  ✅ Code updated${NC}"
    
    # ═══════════════════════════════════════════════════════
    # Phase 3: Dependencies
    # ═══════════════════════════════════════════════════════
    echo ""
    echo -e "${YELLOW}[Phase 3/5] Updating Dependencies${NC}"
    
    source "$VENV_DIR/bin/activate"
    if pip install -r requirements.txt > /dev/null 2>&1; then
        echo -e "${GREEN}  ✅ Dependencies updated${NC}"
    else
        echo -e "${YELLOW}  ⚠️  Some dependencies may have failed${NC}"
    fi
    
    # ═══════════════════════════════════════════════════════
    # Phase 4: Database Migration
    # ═══════════════════════════════════════════════════════
    echo ""
    echo -e "${YELLOW}[Phase 4/5] Database Migration${NC}"
    
    # Check for database reset flag
    if [ -f "$BOT_DIR/.reset_database" ]; then
        echo -e "${YELLOW}  ⚠️  Database reset requested...${NC}"
        rm -f "$BOT_DIR/vpn_bot.db"
        rm -f "$BOT_DIR/.reset_database"
        echo -e "${GREEN}  ✅ Database removed (will recreate on startup)${NC}"
    else
        echo -e "${GREEN}  ✅ Database preserved${NC}"
    fi
    
    # ═══════════════════════════════════════════════════════
    # Phase 5: Restart & Verify
    # ═══════════════════════════════════════════════════════
    echo ""
    echo -e "${YELLOW}[Phase 5/5] Restarting Service${NC}"
    
    systemctl start $SERVICE_NAME
    sleep 3
    
    if systemctl is-active --quiet $SERVICE_NAME; then
        echo -e "${GREEN}  ✅ Service started successfully${NC}"
        
        # Cleanup old backups (keep last 5)
        ls -dt "$BOT_DIR/backups"/update_* 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
        
        echo ""
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}           ✨ UPDATE COMPLETE ✨${NC}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo -e "${CYAN}Backup saved at: ${BACKUP_DIR}${NC}"
    else
        echo -e "${RED}  ❌ Service failed to start!${NC}"
        echo -e "${YELLOW}Rolling back to previous version...${NC}"
        rollback_update "$BACKUP_DIR"
        return 1
    fi
    
    pause
}

backup_database() {
    show_header
    echo -e "${BLUE}💾 Database Backup${NC}"
    echo ""
    
    BACKUP_NAME="vpn_bot_backup_$(date +%Y%m%d_%H%M%S).db"
    BACKUP_PATH="$HOME/$BACKUP_NAME"
    
    if [ -f "$BOT_DIR/vpn_bot.db" ]; then
        cp "$BOT_DIR/vpn_bot.db" "$BACKUP_PATH"
        echo -e "${GREEN}✅ Backup saved to: ${CYAN}${BACKUP_PATH}${NC}"
    else
        echo -e "${YELLOW}⚠️  Database file not found.${NC}"
    fi
    pause
}

restore_database() {
    show_header
    echo -e "${BLUE}📥 Database Restore${NC}"
    echo ""
    
    echo -e "${YELLOW}Enter path to backup file:${NC}"
    read -p "📁 Path: " BACKUP_PATH
    
    if [ ! -f "$BACKUP_PATH" ]; then
        echo -e "${RED}❌ File not found.${NC}"
        pause
        return
    fi
    
    echo -e "${RED}⚠️  WARNING: This will replace your current database!${NC}"
    read -p "Are you sure? (type 'yes' to confirm): " CONFIRM
    
    if [ "$CONFIRM" != "yes" ]; then
        echo -e "${YELLOW}Cancelled.${NC}"
        pause
        return
    fi
    
    # Stop bot
    systemctl stop $SERVICE_NAME
    
    # Backup current
    if [ -f "$BOT_DIR/vpn_bot.db" ]; then
        mv "$BOT_DIR/vpn_bot.db" "$BOT_DIR/vpn_bot.db.old"
    fi
    
    # Restore
    cp "$BACKUP_PATH" "$BOT_DIR/vpn_bot.db"
    
    # Restart
    systemctl start $SERVICE_NAME
    
    echo -e "${GREEN}✅ Database restored successfully!${NC}"
    pause
}

uninstall_bot() {
    show_header
    echo -e "${RED}🗑  UNINSTALL BOT${NC}"
    echo ""
    echo -e "${YELLOW}⚠️  This will:${NC}"
    echo "   - Stop and remove the systemd service"
    echo "   - Remove the global 'vpnbot' command"
    echo "   - The $BOT_DIR folder will NOT be deleted (manual removal required)"
    echo ""
    
    read -p "Are you SURE? (type 'uninstall' to confirm): " CONFIRM
    
    if [ "$CONFIRM" != "uninstall" ]; then
        echo -e "${YELLOW}Cancelled.${NC}"
        pause
        return
    fi
    
    # Stop and disable service
    systemctl stop $SERVICE_NAME 2>/dev/null || true
    systemctl disable $SERVICE_NAME 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    
    # Remove global command
    rm -f /usr/local/bin/vpnbot
    
    echo -e "${GREEN}✅ Service uninstalled.${NC}"
    echo -e "${YELLOW}📁 Data folder preserved at: ${BOT_DIR}${NC}"
    echo -e "${YELLOW}   Delete manually with: rm -rf ${BOT_DIR}${NC}"
    pause
    exit 0
}

# =====================================================
# MENUS
# =====================================================

advanced_menu() {
    while true; do
        show_header
        echo -e "${MAGENTA}⚙️  ADVANCED OPTIONS${NC}"
        echo ""
        echo -e "1) ${CYAN}🔄 Update Bot (Multi-Phase)${NC}"
        echo -e "2) ${CYAN}💾 Backup Database${NC}"
        echo -e "3) ${CYAN}📥 Restore Database${NC}"
        echo -e "4) ${CYAN}🔐 Regenerate Encryption Key${NC}"
        echo -e "5) ${YELLOW}🗑  Reset Database (next update)${NC}"
        echo -e "6) ${RED}🗑  Uninstall Bot${NC}"
        echo -e "7) ${WHITE}🔙 Back to Main Menu${NC}"
        echo ""
        read -p "Select option [1-7]: " CHOICE
        
        case $CHOICE in
            1) update_bot ;;
            2) backup_database ;;
            3) restore_database ;;
            4)
                NEW_KEY=$(openssl rand -base64 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_urlsafe(32))")
                echo -e "${GREEN}New key: ${CYAN}${NEW_KEY}${NC}"
                echo -e "${YELLOW}Update ENCRYPTION_KEY in .env file manually.${NC}"
                pause
                ;;
            5)
                echo ""
                echo -e "${YELLOW}⚠️  DATABASE RESET WARNING${NC}"
                echo -e "${RED}This will DELETE the database on your NEXT UPDATE.${NC}"
                echo -e "All users, subscriptions, transactions, and settings will be LOST."
                echo ""
                read -p "Are you sure? (type 'reset' to confirm): " CONFIRM
                if [ "$CONFIRM" == "reset" ]; then
                    touch "$BOT_DIR/.reset_database"
                    echo -e "${GREEN}✅ Database reset flag set.${NC}"
                    echo -e "${YELLOW}The database will be deleted on your next update.${NC}"
                    echo -e "${YELLOW}Remove the flag with: rm $BOT_DIR/.reset_database${NC}"
                else
                    echo -e "${YELLOW}Cancelled.${NC}"
                fi
                pause
                ;;
            6) uninstall_bot ;;
            7) return ;;
            *) echo -e "${RED}Invalid option!${NC}"; sleep 1 ;;
        esac
    done
}

main_menu() {
    while true; do
        show_header
        echo -e "${YELLOW}📋 MAIN MENU${NC}"
        echo ""
        echo -e "1) ${GREEN}▶️  Start Bot${NC}"
        echo -e "2) ${RED}⏹  Stop Bot${NC}"
        echo -e "3) ${BLUE}🔄 Restart Bot${NC}"
        echo -e "4) ${CYAN}📜 View Live Logs${NC}"
        echo -e "5) ${CYAN}📊 Check Status${NC}"
        echo -e "6) ${YELLOW}✏️  Edit Configuration${NC}"
        echo -e "7) ${MAGENTA}⚙️  Advanced Options${NC}"
        echo -e "8) ${WHITE}🚪 Exit${NC}"
        echo ""
        read -p "Select option [1-8]: " CHOICE
        
        case $CHOICE in
            1) start_bot ;;
            2) stop_bot ;;
            3) restart_bot ;;
            4) view_logs ;;
            5) view_status ;;
            6) edit_config ;;
            7) advanced_menu ;;
            8) 
                echo -e "${GREEN}👋 Goodbye!${NC}"
                exit 0 
                ;;
            *) echo -e "${RED}Invalid option!${NC}"; sleep 1 ;;
        esac
    done
}

# =====================================================
# ENTRY POINT
# =====================================================

check_root
cd "$BOT_DIR"

# Check if first run
if [ ! -f "$ENV_FILE" ] || [ ! -f "$SERVICE_FILE" ]; then
    easy_install
else
    main_menu
fi
