#!/usr/bin/env bash
# Развёртывание бота zoomer на Ubuntu 24.04 LTS: PostgreSQL, venv, systemd.
# Код и .env: /root/zoomer, сервис systemd: zoomer, запуск от root.
#
# На сервере (репозиторий должен лежать в /root/zoomer):
#   bash /root/zoomer/scripts/deploy-vps-ubuntu24.sh
#
# Переменные окружения (опционально), приоритет выше строк в .env:
#   APP_DIR        — каталог приложения (по умолчанию: /root/zoomer)
#   SERVICE_NAME   — имя unit (по умолчанию: zoomer)
#   POSTGRES_USER, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_PASSWORD
#
# Если переменные не заданы в окружении, скрипт читает их из ${APP_DIR}/.env (если файл есть),
# иначе для user/db/host/port — zoomer_bot, localhost, 5432; пароль при необходимости генерируется.
#
# Веб-API: WEB_API_PORT в .env (по умолчанию 8080); при необходимости откройте порт в ufw.

set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Запустите от root: sudo bash $0" >&2; exit 1; }

APP_DIR="${APP_DIR:-/root/zoomer}"
SERVICE_NAME="${SERVICE_NAME:-zoomer}"
ENV_FILE="${APP_DIR}/.env"

POSTGRES_USER="${POSTGRES_USER:-}"
POSTGRES_DB="${POSTGRES_DB:-}"
POSTGRES_HOST="${POSTGRES_HOST:-}"
POSTGRES_PORT="${POSTGRES_PORT:-}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

load_postgres_from_envfile() {
  [[ -f "${ENV_FILE}" ]] || return 0
  local key val line
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" =~ ^POSTGRES_(USER|PASSWORD|DB|HOST|PORT)= ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    val="${val%$'\r'}"
    case "${key}" in
      POSTGRES_USER)     [[ -z "${POSTGRES_USER}" ]] && POSTGRES_USER="${val}" ;;
      POSTGRES_PASSWORD) [[ -z "${POSTGRES_PASSWORD}" ]] && POSTGRES_PASSWORD="${val}" ;;
      POSTGRES_DB)       [[ -z "${POSTGRES_DB}" ]] && POSTGRES_DB="${val}" ;;
      POSTGRES_HOST)     [[ -z "${POSTGRES_HOST}" ]] && POSTGRES_HOST="${val}" ;;
      POSTGRES_PORT)     [[ -z "${POSTGRES_PORT}" ]] && POSTGRES_PORT="${val}" ;;
    esac
  done <"${ENV_FILE}"
}

load_postgres_from_envfile

POSTGRES_USER="${POSTGRES_USER:-zoomer_bot}"
POSTGRES_DB="${POSTGRES_DB:-zoomer_bot}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Не найдена команда: $1" >&2; exit 1; }
}

echo "[1/6] Установка пакетов (apt)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 \
  python3-venv \
  python3-dev \
  build-essential \
  postgresql \
  postgresql-contrib \
  libpq-dev \
  curl \
  >/dev/null

require_cmd psql
require_cmd python3

systemctl enable --now postgresql >/dev/null 2>&1 || systemctl start postgresql

echo "[2/6] Каталог приложения ${APP_DIR}..."
mkdir -p "${APP_DIR}"
[[ -f "${APP_DIR}/main.py" ]] || {
  echo "В ${APP_DIR} нет main.py — разместите проект zoomer в ${APP_DIR}." >&2
  exit 1
}
[[ -f "${APP_DIR}/requirements.txt" ]] || {
  echo "В ${APP_DIR} нет requirements.txt." >&2
  exit 1
}

echo "[3/6] PostgreSQL: роль и база..."
sudo -u postgres psql -v ON_ERROR_STOP=1 -qtAc "SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'" | grep -q 1 \
  || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE \"${POSTGRES_USER}\" WITH LOGIN;"

if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 16)"
  echo "Сгенерирован POSTGRES_PASSWORD (будет в .env)."
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -v "pwd=${POSTGRES_PASSWORD}" \
  -c "ALTER ROLE \"${POSTGRES_USER}\" WITH PASSWORD :'pwd';"

sudo -u postgres psql -v ON_ERROR_STOP=1 -qtAc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 \
  || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";"

sudo -u postgres psql -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE \"${POSTGRES_DB}\" TO \"${POSTGRES_USER}\";"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${POSTGRES_DB}" -c "
  GRANT ALL ON SCHEMA public TO \"${POSTGRES_USER}\";
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"${POSTGRES_USER}\";
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO \"${POSTGRES_USER}\";
" 2>/dev/null || true

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[4/6] Создание ${ENV_FILE} (шаблон)..."
  umask 077
  cat >"${ENV_FILE}" <<EOF
# --- База (заполнено скриптом развёртывания) ---
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=${POSTGRES_PORT}

# --- Обязательно задайте перед production ---
TG_TOKEN=
# Формат: 111, 222 (через запятую и пробел, как в config.py)
ADMIN_IDS=

CHANEL_ID=0

WEB_API_PORT=8080

# Остальные переменные см. config.py / ваш рабочий .env
EOF
  echo "Отредактируйте ${ENV_FILE}, затем: systemctl restart ${SERVICE_NAME}"
else
  echo "[4/6] ${ENV_FILE} уже есть — не перезаписываю."
  if ! grep -q '^POSTGRES_PASSWORD=' "${ENV_FILE}" 2>/dev/null; then
    {
      echo ""
      echo "# Добавлено deploy-vps-ubuntu24.sh"
      echo "POSTGRES_USER=${POSTGRES_USER}"
      echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
      echo "POSTGRES_DB=${POSTGRES_DB}"
      echo "POSTGRES_HOST=${POSTGRES_HOST}"
      echo "POSTGRES_PORT=${POSTGRES_PORT}"
    } >>"${ENV_FILE}"
    echo "В ${ENV_FILE} добавлены переменные Postgres."
  fi
fi

echo "[5/6] Python venv и зависимости..."
cd "${APP_DIR}"
python3 -m venv venv
./venv/bin/pip install --upgrade pip setuptools wheel -q
./venv/bin/pip install -r requirements.txt -q

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
echo "[6/6] systemd unit: ${UNIT_PATH}"

cat >"${UNIT_PATH}" <<EOF
[Unit]
Description=zoomer — Telegram bot and web API
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

if grep -q '^TG_TOKEN=$' "${ENV_FILE}" 2>/dev/null || ! grep -q '^TG_TOKEN=.' "${ENV_FILE}" 2>/dev/null; then
  echo ""
  echo "Внимание: TG_TOKEN в .env пуст или отсутствует — сервис не запущен."
  echo "Заполните ${ENV_FILE}, затем: systemctl start ${SERVICE_NAME}"
else
  systemctl restart "${SERVICE_NAME}.service" || true
  sleep 1
  systemctl --no-pager -l status "${SERVICE_NAME}.service" || true
fi

echo ""
echo "Готово."
echo "  Логи: journalctl -u ${SERVICE_NAME} -f"
echo "  Статус: systemctl status ${SERVICE_NAME}"
