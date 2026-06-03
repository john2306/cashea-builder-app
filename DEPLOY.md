# Despliegue en producción — DigitalOcean (izideploy.com)

Plataforma en un **Droplet con Docker**. Traefik termina TLS y enruta:
- **Builder + API** → `https://izideploy.com`
- **Apps generadas** → `https://<slug>.app.izideploy.com`

Los certificados son **wildcard** vía Let's Encrypt **DNS-01 con DigitalOcean** (un solo cert
`*.app.izideploy.com` sirve a todas las apps).

---

## 1) DNS (en DigitalOcean → Networking → Domains → izideploy.com)
Creá estos records apuntando a la IP del Droplet (la completás en el paso 3):

| Tipo | Host          | Valor            |
|------|---------------|------------------|
| A    | `@`           | `IP_DEL_DROPLET` |
| A    | `*.app`       | `IP_DEL_DROPLET` |

(Opcional `A www → IP` si querés `www`.) El `*.app` es el **wildcard** de las apps.

## 2) Token de API de DigitalOcean (para el cert wildcard DNS-01)
DigitalOcean → **API → Tokens → Generate New Token** (scope: **Write**, incluye DNS).
Guardalo: va en `DO_AUTH_TOKEN` del `.env.prod`.

## 3) Crear el Droplet
- **Imagen**: Ubuntu 24.04 LTS (o el "Docker on Ubuntu" del Marketplace, ya trae Docker).
- **Tamaño**: mínimo **4 GB RAM / 2 vCPU** (Postgres + Redis + backend + worker + builds + apps).
  Si vas a tener muchas apps, 8 GB.
- **Auth**: tu llave SSH.
- Anotá la **IP pública** → ponela en los records DNS del paso 1.

## 4) Firewall (DigitalOcean → Networking → Firewalls)
Inbound permitido: **22 (SSH)**, **80 (HTTP)**, **443 (HTTPS)**. Todo lo demás cerrado
(Postgres/Redis NO se exponen: quedan internos en Docker).

## 5) Instalar Docker (si NO usaste la imagen del Marketplace)
```bash
curl -fsSL https://get.docker.com | sh
```

## 6) Traer el código + configurar
```bash
git clone <tu-repo> izideploy && cd izideploy        # o subí el código por scp/rsync
cp .env.prod.example .env.prod
nano .env.prod                                        # completá TODO (ver abajo)
```
En `.env.prod` (mínimos imprescindibles):
- `BUILDER_DOMAIN=izideploy.com`, `APP_DOMAIN=app.izideploy.com`, `PUBLIC_BASE_URL=https://izideploy.com`
- `ACME_EMAIL=...`, `DO_AUTH_TOKEN=dop_v1_...`
- `POSTGRES_PASSWORD=...` (fuerte), `SESSION_SECRET=$(openssl rand -hex 48)`, `ADMIN_EMAILS=...`
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`
- `GOOGLE_CLIENT_ID/SECRET` (+ Notion/Slack si los usás)

## 7) Levantar la plataforma
```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```
Traefik pedirá los certificados (puede tardar 1–2 min la primera vez). Verificá:
```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f traefik   # ver emisión de certs
```
Probá: `https://izideploy.com` → pantalla de login Google.

## 8) OAuth redirect URIs (con el dominio real)
En **Google Cloud Console** (OAuth client) → Authorized redirect URIs:
- `https://izideploy.com/auth/google/callback`
- `https://izideploy.com/api/mcp/oauth/callback`

En **Notion** (integración pública), redirect URI:
- `https://izideploy.com/api/mcp/oauth/callback`

(El consentimiento de Google debe tener los scopes ya usados; agregá `izideploy.com` a dominios autorizados.)

## 9) Verificación end-to-end
1. Login en `https://izideploy.com` con un correo de `ADMIN_EMAILS`.
2. **Connectors** → conectá lo que uses (Google/Notion/…). 
3. Creá una app simple y **Desplegá** → debería quedar en `https://<slug>.app.izideploy.com`
   con su propio cert (cubierto por el wildcard).

---

## Operación
- **Logs**: `docker compose -f docker-compose.prod.yml logs -f backend worker`
- **Actualizar la plataforma** (nuevo código):
  ```bash
  git pull
  docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build backend worker frontend
  ```
- **Backups**: volumen `pgdata` (Postgres) y `appsdata` (repos git por app). Programá un
  `pg_dump` periódico y snapshots del Droplet en DO.

## Notas / troubleshooting
- **Certs no emiten**: revisá `DO_AUTH_TOKEN` (scope Write) y que el DNS `*.app` y `@` ya
  resuelvan a la IP (`dig izideploy.com`, `dig foo.app.izideploy.com`).
- **Build de apps falla por DNS (pip/npm)** dentro de Docker: en el Droplet suele andar; si no,
  exportá `DOCKER_BUILDKIT=0` antes del `up`.
- **Seguridad**: la plataforma ejecuta código generado en contenedores en el mismo host
  (vía docker.sock). El acceso al builder está detrás de Google SSO + allowlist de correos;
  mantené `ADMIN_EMAILS` acotado y no compartas apps con desconocidos.
- **Memoria**: cada app desplegada es un contenedor; si se acumulan, escalá el Droplet o
  apagá apps que no uses (se borran desde **Apps → ⋯ → Eliminar**).
