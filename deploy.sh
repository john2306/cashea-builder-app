#!/usr/bin/env bash
# Deploy automático de la plataforma (Cashea Hub / IziDeploy) en un Droplet Ubuntu/Debian.
#
#   Primer uso en el Droplet:
#     git clone <tu-repo> izideploy && cd izideploy
#     cp .env.prod.example .env.prod && nano .env.prod   # completá tus valores
#     chmod +x deploy.sh && ./deploy.sh
#
#   Actualizar (re-deploy): ./deploy.sh   (hace git pull + rebuild + up)
#
# Instala Docker + plugin compose si faltan. Idempotente: seguro de re-correr.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"

# sudo solo si no somos root
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

log() { printf "\n\033[1;33m▶ %s\033[0m\n" "$*"; }
err() { printf "\n\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

# 1) Docker
if ! command -v docker >/dev/null 2>&1; then
  log "Docker no está instalado. Instalando (get.docker.com)…"
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO systemctl enable --now docker || true
else
  log "Docker presente: $(docker --version)"
fi

# 2) Plugin docker compose
if ! $SUDO docker compose version >/dev/null 2>&1; then
  log "Instalando el plugin de docker compose…"
  $SUDO apt-get update -y && $SUDO apt-get install -y docker-compose-plugin
fi

# 3) .env.prod (si no existe, lo creo del ejemplo y freno para que lo completes)
if [ ! -f "$ENV_FILE" ]; then
  if [ -f ".env.prod.example" ]; then
    cp .env.prod.example "$ENV_FILE"
    err "Creé $ENV_FILE desde el ejemplo. EDITALO con tus valores (dominios, DO_AUTH_TOKEN, secrets, API keys) y volvé a correr ./deploy.sh"
    exit 1
  fi
  err "Falta $ENV_FILE y no hay .env.prod.example."; exit 1
fi

# Chequeo mínimo: que no queden placeholders críticos
if grep -qE '^(DO_AUTH_TOKEN=dop_v1_xxx|SESSION_SECRET=cambia|POSTGRES_PASSWORD=pon-un)' "$ENV_FILE"; then
  err "Hay valores de ejemplo sin completar en $ENV_FILE (DO_AUTH_TOKEN / SESSION_SECRET / POSTGRES_PASSWORD). Completalos."
  exit 1
fi

# 4) Últimos cambios (si es un clon git)
if [ -d .git ]; then
  log "git pull…"
  git pull --ff-only || err "git pull falló (continúo con el código local)."
fi

# 5) Build + up
log "Construyendo y levantando el stack (puede tardar la primera vez)…"
$SUDO docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

log "Estado de los servicios:"
$SUDO docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

DOM="$(grep -E '^BUILDER_DOMAIN=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"
printf "\n\033[1;32m✅ Deploy lanzado.\033[0m\n"
printf "   Builder:  https://%s\n" "${DOM:-tu-dominio}"
printf "   Certs (1ra vez tardan 1-2 min):  %s docker compose --env-file %s -f %s logs -f traefik\n" "$SUDO" "$ENV_FILE" "$COMPOSE_FILE"
printf "   Logs app: %s docker compose --env-file %s -f %s logs -f backend worker\n" "$SUDO" "$ENV_FILE" "$COMPOSE_FILE"
