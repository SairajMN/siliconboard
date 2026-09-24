#!/usr/bin/env bash
# siliconboard host bootstrap script (Ubuntu 24.04 / Debian 12)
# Installs system dependencies, Docker, Python venv, and builds the EDA tools image.
set -euo pipefail

echo "==> SiliconBoard Host Setup"
if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root (or via sudo)"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> Updating package indices"
apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-venv python3-pip docker.io

echo "==> Ensuring Docker service is active"
systemctl enable --now docker

APP_DIR="/opt/siliconboard"
ENV_DIR="/etc/siliconboard"

echo "==> Setting up directories"
mkdir -p "${APP_DIR}"
mkdir -p "${ENV_DIR}"
chmod 700 "${ENV_DIR}"

if [ ! -f "${ENV_DIR}/env" ]; then
    echo "==> Creating template ${ENV_DIR}/env (mode 0600)"
    cat > "${ENV_DIR}/env" <<'EOF'
# /etc/siliconboard/env (mode 0600)
# Add your Gemini / Groq / Nvidia API keys here:
# GEMINI_API_KEY_1=...
# GROQ_API_KEY_1=...
# NVIDIA_API_KEY=...
EOF
    chmod 600 "${ENV_DIR}/env"
fi

if [ -d "${APP_DIR}/docker/eda" ]; then
    echo "==> Building EDA docker image on host"
    docker build -t siliconboard/eda:1.0 "${APP_DIR}/docker/eda"
fi

if [ -f "${APP_DIR}/requirements.txt" ]; then
    echo "==> Setting up python venv in ${APP_DIR}/.venv"
    python3 -m venv "${APP_DIR}/.venv"
    "${APP_DIR}/.venv/bin/pip" install --upgrade pip
    "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
fi

if [ -f "${APP_DIR}/deploy/siliconboard.service" ]; then
    echo "==> Installing systemd service"
    cp "${APP_DIR}/deploy/siliconboard.service" /etc/systemd/system/siliconboard.service
    systemctl daemon-reload
    systemctl enable siliconboard.service
    echo "==> Systemd service installed. Start with: systemctl start siliconboard"
fi

echo "==> Setup completed successfully."
