#!/bin/bash
# =====================================================
# VPN Telegram Bot - Linux installer (Git clone OR local/FTP)
# =====================================================
# Git (default):  sudo bash install.sh
#                 sudo bash install.sh /opt/vpn_bot
# Local/FTP:      cd /path/to/uploaded/vpn_bot && sudo bash install.sh
# Custom git URL: sudo bash install.sh --git https://github.com/user/vpn_bot.git /srv/vpn_bot
# Skip local DB:  sudo bash install.sh --skip-db
# =====================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_GIT_DIR="/opt/vpn_bot"
REPO_URL="https://github.com/VeilVulp/vpn_bot.git"
SKIP_DB=0
MODE=""
INSTALL_DIR=""

usage() {
    echo "Usage:"
    echo "  sudo bash install.sh                          # git clone -> /opt/vpn_bot"
    echo "  sudo bash install.sh /path/to/install        # git clone -> /path"
    echo "  sudo bash install.sh --git [URL] [DIR]        # explicit git mode"
    echo "  cd <project> && sudo bash install.sh          # local/FTP (current directory)"
    echo "  sudo bash install.sh --skip-db                # skip PostgreSQL bootstrap"
    exit 0
}

is_project_dir() {
    local d="$1"
    [ -f "$d/manage.sh" ] && { [ -f "$d/vpn_bot/__main__.py" ] || [ -f "$d/pyproject.toml" ]; }
}

write_install_conf() {
    mkdir -p /etc/vpnbot
    echo "INSTALL_DIR=$INSTALL_DIR" > /etc/vpnbot/install.conf
    chmod 644 /etc/vpnbot/install.conf
}

install_system_deps() {
    echo -e "${BLUE}[1/6] Installing system dependencies...${NC}"
    apt update -y > /dev/null 2>&1
    apt install -y python3 python3-pip python3-venv git curl wget postgresql postgresql-contrib openssl > /dev/null 2>&1
    echo -e "${GREEN}Dependencies installed.${NC}"
}

setup_postgresql() {
    if [ "$SKIP_DB" -eq 1 ]; then
        echo -e "${YELLOW}Skipping PostgreSQL setup (--skip-db).${NC}"
        return
    fi
    echo -e "${BLUE}[2/6] Setting up PostgreSQL...${NC}"
    systemctl start postgresql 2>/dev/null || true
    systemctl enable postgresql 2>/dev/null || true
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = 'vpnbot'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE USER vpnbot WITH PASSWORD 'vpnbot';" 2>/dev/null
    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'vpnbot'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE DATABASE vpnbot OWNER vpnbot;" 2>/dev/null
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE vpnbot TO vpnbot;" 2>/dev/null
    echo -e "${GREEN}PostgreSQL ready (user/db: vpnbot).${NC}"
}

clone_or_use_local() {
    if [ "$MODE" = "local" ]; then
        INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR}"
        INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd)"
        echo -e "${BLUE}[3/6] Using local project at ${INSTALL_DIR}${NC}"
        if ! is_project_dir "$INSTALL_DIR"; then
            echo -e "${RED}Not a valid vpn_bot project directory.${NC}"
            exit 1
        fi
        echo -e "${GREEN}Local project OK.${NC}"
        return
    fi

    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_GIT_DIR}"
    echo -e "${BLUE}[3/6] Cloning repository to ${INSTALL_DIR}...${NC}"

    if [ -d "$INSTALL_DIR/.git" ]; then
        cd "$INSTALL_DIR"
        git pull origin main || git pull origin master || true
    elif [ -d "$INSTALL_DIR" ] && is_project_dir "$INSTALL_DIR"; then
        echo -e "${YELLOW}Directory exists with project files; using as-is (no git).${NC}"
    elif [ -d "$INSTALL_DIR" ]; then
        echo -e "${RED}Directory exists but is not a vpn_bot project. Remove it or choose another path.${NC}"
        exit 1
    else
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
    echo -e "${GREEN}Project ready at ${INSTALL_DIR}${NC}"
}

setup_python_env() {
    echo -e "${BLUE}[4/6] Python virtualenv + package install...${NC}"
    cd "$INSTALL_DIR"
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install --upgrade pip > /dev/null 2>&1
    pip install -r requirements.txt > /dev/null 2>&1
    pip install -e . > /dev/null 2>&1
    echo -e "${GREEN}Python environment ready (pip install -e .).${NC}"
}

finalize_permissions() {
    echo -e "${BLUE}[5/6] Permissions and install path...${NC}"
    chmod +x manage.sh install.sh 2>/dev/null || true
    write_install_conf

    cat > /usr/local/bin/vpnbot << EOF
#!/bin/bash
INSTALL_DIR=\$(grep -E '^INSTALL_DIR=' /etc/vpnbot/install.conf 2>/dev/null | cut -d= -f2-)
INSTALL_DIR=\${INSTALL_DIR:-$INSTALL_DIR}
cd "\$INSTALL_DIR" && exec ./manage.sh "\$@"
EOF
    chmod +x /usr/local/bin/vpnbot
    echo -e "${GREEN}Global command: vpnbot${NC}"
}

run_bootstrap() {
    echo -e "${BLUE}[6/6] Service bootstrap (systemd)...${NC}"
    cd "$INSTALL_DIR"
    if [ -f .env ]; then
        INSTALL_NONINTERACTIVE=1 ./manage.sh --bootstrap
    else
        echo -e "${YELLOW}.env not found — run configure_env via: sudo vpnbot${NC}"
        INSTALL_NONINTERACTIVE=1 SKIP_ENV_WIZARD=1 ./manage.sh --bootstrap || ./manage.sh --bootstrap
    fi
}

# --- Parse args ---
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage ;;
        --skip-db) SKIP_DB=1; shift ;;
        --git)
            MODE="git"
            shift
            [ -n "$1" ] && [[ "$1" != --* ]] && { REPO_URL="$1"; shift; }
            [ -n "$1" ] && [[ "$1" != --* ]] && { INSTALL_DIR="$1"; shift; }
            ;;
        *)
            if [ -z "$INSTALL_DIR" ]; then
                INSTALL_DIR="$1"
            fi
            shift
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}Run with sudo: sudo bash install.sh${NC}"
    exit 1
fi

clear
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}        VPN TELEGRAM BOT - INSTALLER${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ -z "$MODE" ]; then
    if is_project_dir "$SCRIPT_DIR"; then
        MODE="local"
        INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR}"
    else
        MODE="git"
    fi
fi

install_system_deps
setup_postgresql
clone_or_use_local
setup_python_env
finalize_permissions
run_bootstrap

echo ""
echo -e "${GREEN}Installation complete.${NC}"
echo -e "  Directory: ${CYAN}${INSTALL_DIR}${NC}"
echo -e "  Manage:    ${CYAN}sudo vpnbot${NC}"
echo -e "  Service:   ${CYAN}systemctl status vpn_bot${NC}"
echo -e "  Logs:      ${CYAN}journalctl -u vpn_bot -f${NC}"
echo ""
