#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="smart-followup-assistant"
ENV_FILE="${PROJECT_DIR}/.env.server"
VENV_DIR="${PROJECT_DIR}/.venv"
DATA_DIR="${PROJECT_DIR}/data"
STATIC_DIR="${PROJECT_DIR}/staticfiles"
DB_FILE="${DATA_DIR}/db.sqlite3"
ROOT_USER_NAME="${FOLLOWUP_ROOT_USER:-root}"

if [[ -n "${FOLLOWUP_ROOT_PASSWORD:-}" ]]; then
  ROOT_PASSWORD="${FOLLOWUP_ROOT_PASSWORD}"
  GENERATED_ROOT_PASSWORD=0
else
  ROOT_PASSWORD="$(python3 - <<'PY'
import secrets
alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*-_"
print("".join(secrets.choice(alphabet) for _ in range(20)))
PY
)"
  GENERATED_ROOT_PASSWORD=1
fi

if command -v sudo >/dev/null 2>&1; then
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
  else
    SUDO="sudo"
  fi
else
  SUDO=""
fi

RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_GROUP="$(id -gn "${RUN_USER}")"

detect_public_ip() {
  curl -fsSL https://api.ipify.org 2>/dev/null \
    || curl -fsSL https://ipv4.icanhazip.com 2>/dev/null \
    || hostname -I | awk '{print $1}'
}

generate_secret_key() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(50))
PY
}

PUBLIC_IP="$(detect_public_ip | tr -d '[:space:]')"
if [[ -z "${PUBLIC_IP}" ]]; then
  echo "Unable to detect a public IP address automatically."
  echo "Create .env.server manually and set FOLLOWUP_ALLOWED_HOSTS before retrying."
  exit 1
fi

echo "1/8 Installing system packages"
${SUDO} apt-get update
${SUDO} apt-get install -y python3 python3-venv python3-pip nginx curl

echo "2/8 Preparing virtual environment"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"

echo "3/8 Preparing runtime directories"
mkdir -p "${DATA_DIR}" "${STATIC_DIR}" "${PROJECT_DIR}/runtime"

if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<EOF
FOLLOWUP_DEBUG=false
DJANGO_SECRET_KEY=$(generate_secret_key)
FOLLOWUP_DB_PATH=${DB_FILE}
FOLLOWUP_ALLOWED_HOSTS=127.0.0.1,localhost,${PUBLIC_IP}
FOLLOWUP_CSRF_TRUSTED_ORIGINS=http://${PUBLIC_IP}
EOF
fi

echo "4/8 Running database migrations"
"${VENV_DIR}/bin/python" "${PROJECT_DIR}/manage.py" migrate --noinput

echo "5/8 Collecting static files"
"${VENV_DIR}/bin/python" "${PROJECT_DIR}/manage.py" collectstatic --noinput

echo "6/8 Ensuring root account exists"
"${VENV_DIR}/bin/python" "${PROJECT_DIR}/manage.py" ensure_root_account --username "${ROOT_USER_NAME}" --password "${ROOT_PASSWORD}"

echo "7/8 Writing systemd service"
${SUDO} tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=Smart Follow-up Assistant Django Service
After=network.target

[Service]
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "8/8 Writing Nginx configuration and starting services"
${SUDO} tee "/etc/nginx/sites-available/${SERVICE_NAME}.conf" >/dev/null <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 20m;

    location /static/ {
        alias ${STATIC_DIR}/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

${SUDO} ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}.conf" "/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"
if [[ -f /etc/nginx/sites-enabled/default ]]; then
  ${SUDO} rm -f /etc/nginx/sites-enabled/default
fi

${SUDO} chown -R "${RUN_USER}:${RUN_GROUP}" "${PROJECT_DIR}"
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable "${SERVICE_NAME}"
${SUDO} systemctl restart "${SERVICE_NAME}"
${SUDO} nginx -t
${SUDO} systemctl restart nginx

echo
echo "Deployment complete."
echo "URL: http://${PUBLIC_IP}/"
echo "Root username: ${ROOT_USER_NAME}"
echo "Root password: ${ROOT_PASSWORD}"
if [[ "${GENERATED_ROOT_PASSWORD}" -eq 1 ]]; then
  echo "A random root password was generated for this deployment."
fi
echo
echo "If the site is not reachable from the internet, allow TCP port 80 in your Alibaba Cloud security group."
