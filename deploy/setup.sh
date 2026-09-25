#!/usr/bin/env bash
# siliconboard host bootstrap (Ubuntu 24.04). Clones the repo, installs deps, builds the EDA images.
# Override for a fork:  REPO_URL=... BRANCH=main sudo -E bash deploy/setup.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/SairajMN/siliconboard.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="/opt/siliconboard"
ENV_DIR="/etc/siliconboard"
SKIP_STA="${SKIP_STA:-0}"   # 1 skips the ~15 min OpenSTA build; timing then reports an estimate

echo "==> SiliconBoard Host Setup"
if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root (or via sudo)"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> packages"
apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-venv python3-pip docker.io

systemctl enable --now docker
mkdir -p "${ENV_DIR}"
chmod 700 "${ENV_DIR}"

if [ ! -d "${APP_DIR}/.git" ]; then
    echo "==> cloning ${REPO_URL} (${BRANCH}) into ${APP_DIR}"
    git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_DIR}"
else
    echo "==> ${APP_DIR} is already a clone, leaving it alone"
fi

if [ ! -f "${ENV_DIR}/env" ]; then
    echo "==> writing ${ENV_DIR}/env template (mode 0600)"
    cat > "${ENV_DIR}/env" <<'ENVEOF'
GEMINI_API_KEY_1=
GEMINI_API_KEY_2=
GEMINI_API_KEY_3=
GEMINI_API_KEY_4=
GROQ_API_KEY_1=
GROQ_API_KEY_2=
GROQ_API_KEY_3=
GROQ_API_KEY_4=
NVIDIA_API_KEY=
ENVEOF
    chmod 600 "${ENV_DIR}/env"
    echo "    add your keys, then: systemctl start siliconboard"
fi

echo "==> venv + dependencies"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install -q --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

echo "==> building the EDA image (verilator, yosys, iverilog)"
docker build -q -t siliconboard/eda:1.0 "${APP_DIR}/docker/eda"

echo "==> fetching the sky130 liberty for the timing stage"
# the timing stage is a bonus, so a failed fetch must not abort the whole bootstrap
( cd "${APP_DIR}" && "${APP_DIR}/.venv/bin/python" pdk/fetch_liberty.py ) \
    || echo "[!] liberty fetch failed; the timing stage will report an estimate"

if [ "${SKIP_STA}" = "1" ]; then
    echo "==> SKIP_STA=1, not building OpenSTA; the timing stage will report an estimate"
else
    echo "==> building OpenSTA from source (slow, ~15 min, needs ~2GB disk)"
    docker build -t siliconboard/sta:1.0 "${APP_DIR}/docker/sta" \
        || echo "[!] STA image build failed; the timing stage will fall back to an estimate"
fi

if [ -f "${APP_DIR}/deploy/siliconboard.service" ]; then
    echo "==> installing systemd service"
    cp "${APP_DIR}/deploy/siliconboard.service" /etc/systemd/system/siliconboard.service
    chmod 755 "${APP_DIR}/deploy/check_host.sh"
    systemctl daemon-reload
    systemctl enable siliconboard.service
    echo "    add your keys, then: systemctl start siliconboard"
fi

echo "==> Setup completed successfully."
