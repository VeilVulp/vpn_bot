#!/bin/bash

# =====================================================
# 🚀 VPN Telegram Bot - One-Line Bootstrap Installer
# Repository: https://github.com/VeilVulp/vpn_bot
# =====================================================
# Usage: bash <(curl -sL https://raw.githubusercontent.com/VeilVulp/vpn_bot/main/install.sh)
# =====================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Clear screen and show header
clear
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}        🛰️  VPN TELEGRAM BOT - QUICK INSTALLER${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check for root
if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}⚠️  This script requires root privileges.${NC}"
    echo -e "${YELLOW}   Please run with: sudo bash install.sh${NC}"
    exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    OS_VERSION=$VERSION_ID
else
    echo -e "${RED}❌ Cannot detect operating system.${NC}"
    exit 1
fi

echo -e "${BLUE}📋 Detected OS: ${GREEN}$OS $OS_VERSION${NC}"
echo ""

# Check supported OS
if [[ "$OS" != "ubuntu" && "$OS" != "debian" ]]; then
    echo -e "${YELLOW}⚠️  This installer is optimized for Ubuntu/Debian.${NC}"
    read -p "Continue anyway? (y/n): " CONTINUE
    if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

# Step 1: Update and Install Dependencies
echo -e "${BLUE}[1/4] 📦 Installing system dependencies...${NC}"
apt update -y > /dev/null 2>&1
apt install -y python3 python3-pip python3-venv git curl wget > /dev/null 2>&1
echo -e "${GREEN}✅ Dependencies installed.${NC}"
echo ""

# Step 2: Clone repository
INSTALL_DIR="/opt/vpn_bot"
echo -e "${BLUE}[2/4] 📥 Cloning repository...${NC}"

# Check connectivity first
echo -e "   🔍 Checking GitHub connectivity..."
if ! curl -s --head --request GET https://github.com --connect-timeout 5 > /dev/null; then
    echo -e "${YELLOW}   ⚠️  GitHub seems unreachable. This might cause the clone to fail.${NC}"
fi

# Check if URL provided as argument (for dev/testing)
if [ -n "$1" ]; then
    REPO_URL="$1"
    echo -e "${GREEN}Using custom URL: $REPO_URL${NC}"
else
    REPO_URL="https://github.com/VeilVulp/vpn_bot.git"
fi

if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}⚠️  Directory already exists. Pulling latest changes...${NC}"
    cd "$INSTALL_DIR" || exit 1
    
    # Check if it's a valid git repo
    if [ -d ".git" ]; then
        if ! git pull origin main; then
            echo -e "${RED}❌ Failed to update. Resetting directory...${NC}"
            cd ..
            rm -rf "$INSTALL_DIR"
            echo -e "${GREEN}Cleanup complete. Starting fresh clone...${NC}"
            # No exit 1 here, fall through to the clone block below
        fi
    else
        echo -e "${RED}❌ Directory exists but is not a git repository. Removing...${NC}"
        cd ..
        rm -rf "$INSTALL_DIR"
        # Can proceed to clone now? Yes, but simplest to exit and ask rerun or fallthrough
        echo -e "${GREEN}Cleaned up. Proceeding to clone...${NC}"
    fi
fi

# Clone if directory doesn't exist (or was removed)
if [ ! -d "$INSTALL_DIR" ]; then
    # Use shallow clone for speed (--depth 1)
    if ! git clone --depth 1 --verbose "$REPO_URL" "$INSTALL_DIR"; then
        echo ""
        echo -e "${RED}❌ Failed to clone repository.${NC}"
        echo -e "${YELLOW}Possible causes:${NC}"
        echo "   - Internet connection issues (GitHub blocked?)"
        echo "   - Repository URL is incorrect"
        echo "   - Proxy settings needed"
        echo ""
        echo -e "${YELLOW}Try running manually to debug:${NC}"
        echo -e "   git clone --depth 1 $REPO_URL $INSTALL_DIR"
        exit 1
    fi
fi

cd "$INSTALL_DIR" || exit 1
echo -e "${GREEN}✅ Repository ready at ${INSTALL_DIR}${NC}"
echo ""

# Step 3: Setup Python environment
echo -e "${BLUE}[3/4] 🐍 Setting up Python environment...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1
echo -e "${GREEN}✅ Python environment ready.${NC}"
echo ""

# Step 4: Give permissions
echo -e "${BLUE}[4/4] 🔑 Setting permissions...${NC}"
chmod +x manage.sh
chmod +x install.sh 2>/dev/null || true
echo -e "${GREEN}✅ Permissions set.${NC}"
echo ""

# Create global alias
echo -e "${BLUE}🔧 Creating global command 'vpnbot'...${NC}"
cat > /usr/local/bin/vpnbot << 'EOF'
#!/bin/bash
cd /opt/vpn_bot && sudo ./manage.sh
EOF
chmod +x /usr/local/bin/vpnbot
echo -e "${GREEN}✅ You can now use 'vpnbot' from anywhere!${NC}"
echo ""

# Final message
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}        ✨ INSTALLATION COMPLETE ✨${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}📌 Next Steps:${NC}"
echo -e "   1. Run ${CYAN}sudo vpnbot${NC} to open management menu"
echo -e "   2. Configure your bot settings (.env file)"
echo -e "   3. Start the bot service"
echo ""
echo -e "${BLUE}📁 Installation Directory: ${CYAN}${INSTALL_DIR}${NC}"
echo -e "${BLUE}📖 Documentation: ${CYAN}https://github.com/VeilVulp/vpn_bot${NC}"
echo ""

# Launch manager
read -p "🚀 Open management menu now? (y/n): " LAUNCH
if [[ "$LAUNCH" =~ ^[Yy]$ ]]; then
    ./manage.sh
fi
