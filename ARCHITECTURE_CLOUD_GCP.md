# Arquitectura cloud-native en GCP — Cashea Hub

> **Estado:** propuesta para aprobación.

Arquitectura **serverless en Google Cloud** para Cashea Hub, con foco en **alta disponibilidad,
escalabilidad y seguridad** (defensa en profundidad contra amenazas externas **e internas**). Sin VMs
ni SSH; las **apps generadas por IA se tratan como código no confiable** y corren encajonadas en su
propio sandbox, sin acceso a la base de la plataforma.

---

## 0. Resumen ejecutivo

**Qué se propone:** una plataforma serverless (Cloud Run + servicios gestionados) donde cada app
generada es un servicio aislado y autoescalable. La autorización vive en un **control plane** central;
las apps solo obedecen. El compromiso de cualquier app queda **contenido a un solo tenant**: ni la
plataforma, ni la base de datos, ni los secretos, ni otras apps o dueños quedan expuestos.

**Pilares de seguridad:**

- **Cero confianza:** toda request a datos verifica identidad + autorización; nada se confía por red.
- **Privilegio mínimo:** una identidad por servicio; el *build* y el *deploy* tienen identidades
  separadas; las apps casi no tienen permisos.
- **Tenant aislado:** sandbox por app, sin acceso a la DB ni a secretos, con **egreso restringido por
  destino**.
- **Build no confiable endurecido:** el código generado influye en lo que se construye, así que el
  pipeline de build se trata como superficie hostil (privilegio mínimo, sin red, imágenes firmadas).
- **Doble borde:** Cloudflare (WAF/DDoS/bot) delante de todo y **Cloud Armor** en el balanceador,
  cubriendo también `/api`.

**Trade-off honesto:** se gana HA, contención de amenazas y operación gestionada, a cambio de mayor
costo y complejidad de IAM/red.

---

## 1. Principios

- **Sin servidores que administrar y sin SSH** (todo serverless: Cloud Run + servicios gestionados).
- **Cero confianza:** cada componente con identidad mínima; apps generadas aisladas y sin acceso a la
  base de la plataforma.
- **El build también es no confiable:** el pipeline de build se trata como superficie hostil
  (privilegio mínimo, sin red, imágenes firmadas).
- **Red privada por defecto:** bases de datos sin IP pública; **egreso allowlisted por destino**.
- **Doble WAF:** Cloudflare en el borde y Cloud Armor en el LB, incluyendo `/api`.

---

## 2. Diagrama

```
                                       Internet
                                          │
                              ┌───────────▼───────────┐  DNS + WAF + DDoS L3/4/7 + bot
                              │   Cloudflare (edge)    │  (zona cashea.app)
                              │   AOP mTLS custom       │  mTLS de origen con cert PROPIO
                              └──────┬──────────────┬──┘
                hub.cashea.app (SPA) │              │ api.hub.cashea.app  +  *.hub.cashea.app
                                     ▼              ▼ (API)                 (apps)
                   ┌────────────────────────┐   ┌───────────────────────────────────────┐
     sign-in  ┌───▶│  Firebase Hosting (SPA) │   │  Global External HTTPS LB              │
    (ID token │    │  CDN + TLS · origen     │   │  + Cloud Armor (OWASP, rate-limit,     │
     JWT) ▲   │    │  privado (solo estáticos)│   │    Adaptive ML-DDoS, allowlist CF)     │
          │   │    └────────────────────────┘   │  TLS Certificate Manager (wildcard)    │
          │   │                                 └──────┬──────────────────────┬──────────┘
   ┌──────┴───┴──────────────┐                    api  │                *.hub │ → app-router
   │ Firebase Authentication  │                         ▼                      ▼
   │ / Identity Platform      │            ┌────────────────────────┐  ┌──────────────────────────┐
   │ (Google sign-in, MFA,    │            │  builder-api (Cloud Run)│  │ Apps (1 Cloud Run/app)    │
   │  emite/renueva ID tokens)│            │  verifica ID token      │  │ ingress interno · SA mínima│
   └──────────────────────────┘            │  (Admin SDK); control   │  │ SIN acceso a DB/secretos   │
                                           │  plane / gateway SSE    │  │ EGRESO ALLOWLISTED (FQDN)  │
                                           └──┬───────────┬──────┬───┘  └────────────┬──────────────┘
                            deploy SA         │  Pub/Sub / │ VPC  │   connector-proxy (ÚNICO):
                            Cloud Run Admin   │ Cloud Tasks│ conn │   la plataforma ejecuta la tool;
                                   ▼          ▼            ▼      │   NUNCA entrega el token crudo
                                                                  │   X-App-Secret (clave propia)
                       ┌─────────────────┐ ┌──────────┐  ┌───────▼──────── VPC (privada) ───────────┐
                       │  BUILD aislado   │ │  worker  │  │ Cloud SQL (Postgres, HA regional, IP priv)│
                       │  Cloud Build      │ │ Cloud Run│  │ Memorystore Redis (HA) — SSE/pub-sub      │
                       │  • build SA mínima│ │(deploy/QA)│  │ Secret Manager (keys, OAuth, Fernet)      │
                       │  • SIN red        │ └──────────┘  │ VPC Service Controls (perímetro anti-exfil)│
                       │  • sin scripts arb│               │ Cloud NAT (IP estática, egreso plataforma)│
                       │  → Artifact Reg.  │               └──────────────────┬────────────────────────┘
                       │  → Binary Authz   │                                   │ egreso allowlisted
                       └─────────┬─────────┘                    ┌──────────────▼─────────────┐
                                 │ solo imágenes FIRMADAS         │ Secure Web Proxy (FQDN ACL) │
                                 ▼ pueden desplegarse             │ → Anthropic / OAuth (solo   │
                          Cloud Run (apps)  ◀── Binary Authz       │   destinos permitidos)      │
                                                                  └─────────────────────────────┘
   IAP (Identity-Aware Proxy) → endpoints internos/admin. NUNCA se abre SSH (sin VMs).
   Cloud Logging / Monitoring / Trace / Error Reporting / Security Command Center / Audit Logs
   (con gobierno de PII: retención + IAM sobre los propios logs).
```

> **Frontend y auth:** la SPA la sirve **Firebase Hosting** (CDN+TLS propio, origen privado, solo
> estáticos). El login y los tokens los emite **Firebase Authentication / Identity Platform** (ID
> token = JWT firmado por Google); `builder-api` solo **verifica**. `builder-api` se expone en
> `api.hub.cashea.app` **detrás del LB + Cloud Armor**, igual que las apps, para que la superficie más
> sensible (auth, gateway, credenciales de conectores) quede bajo WAF + rate-limit.

---

## 3. Borde e ingreso (capa pública)

| Capa | Servicio | Función |
|---|---|---|
| Edge | **Cloudflare** | DNS, **DDoS** L3/4/7, **WAF**, bot management, caché de estáticos, **mTLS de origen con certificado custom** para que solo Cashea-en-Cloudflare pueda hablar con el LB. |
| Ingress | **Global External Application LB** | Punto único de entrada. URL map por host: `hub.cashea.app`→SPA, `api.hub.cashea.app`→`builder-api`, `*.hub.cashea.app`→router de apps. **Serverless NEGs** a Cloud Run. TLS gestionado por **Certificate Manager**. |
| WAF/GCP | **Cloud Armor** | Reglas **OWASP** (SQLi/XSS/LFI…), **rate-limiting** por IP/usuario, **geo/IP allow-deny**, **Adaptive Protection** (ML anti-DDoS L7), **allowlist de rangos de Cloudflare**. Aplica a `/api` y a las apps. |

**Endurecimiento del origen:** se usa **Authenticated Origin Pulls con certificado custom** (subimos
el nuestro, no el cert compartido de Cloudflare) + allowlist de IPs de CF como segunda capa. Así nadie
llega "por detrás" del edge.

---

## 4. Deploy de apps (sin acceso al host)

1. El **worker** (Cloud Run, disparado por **Pub/Sub** / Cloud Tasks) toma el job de deploy.
2. Genera el código + QA y lanza un **Cloud Build aislado** que construye la imagen y la sube a
   **Artifact Registry** (`apps/app-<slug>`).
3. Una **SA de deploy** despliega/actualiza un **Cloud Run service por app** (`app-<slug>`) vía la
   **Cloud Run Admin API**, con **ingress interno** y **SA de runtime mínima** (sin permisos a la DB ni
   a otros servicios).
4. Enrutamiento `*.hub.cashea.app`: un **router** liviano (Cloud Run) detrás del LB mapea
   `slug → URL interna del Cloud Run de la app`. El router solo enruta; no expone tokens.

Cada app es **un servicio aislado y autoescalable**, no un contenedor compartiendo host.

### 4.bis. El build es superficie hostil

El código de las apps lo genera un LLM (no confiable) y un Dockerfile/hook/`postinstall` malicioso
ejecutaría comandos arbitrarios con la identidad del build. Por eso:

- **Build SA de privilegio mínimo, separada de la SA de deploy:** la SA de build solo escribe en
  Artifact Registry; **no** despliega Cloud Run ni lee secretos. El deploy lo hace una SA distinta
  sobre una imagen **ya construida**.
- **Build sin red de salida** (o con egreso allowlisted): el código no confiable no puede exfiltrar ni
  llamar a C2 durante la construcción.
- **Sin scripts arbitrarios de dependencias** donde sea posible (npm `--ignore-scripts`, lockfiles
  fijos, base images propias y escaneadas).
- **Binary Authorization:** Cloud Run **solo admite imágenes firmadas** por el pipeline verificado y
  escaneadas (Artifact Registry scanning); una imagen manipulada o sin firmar no despliega.

El momento privilegiado (deploy) **no** ejecuta código influido por el tenant.

---

## 5. TLS / certificados (gestionado y auto-renovado)

Dos saltos de TLS, porque Cloudflare es el borde:

**A) Cliente ↔ Cloudflare:** `hub.cashea.app` lo cubre **Universal SSL**; el wildcard de segundo nivel
`*.hub.cashea.app` requiere **Advanced Certificate Manager (ACM)** de Cloudflare.

**B) Cloudflare ↔ origen:**
- **SPA (Firebase Hosting):** certificado gestionado y auto-renovado por Firebase para `hub.cashea.app`.
- **`api.hub.cashea.app` y `*.hub.cashea.app` (tras el LB):** **wildcard `*.hub.cashea.app` con
  Certificate Manager + DNS authorization** (un CNAME de validación una vez en Cloudflare); Certificate
  Manager **emite y auto-renueva** y se adjunta al target HTTPS proxy del LB. Cero propagación o
  renovación manual.
- Alternativa CF↔origen: **Cloudflare Origin CA cert** para modo *Full (strict)*.

Las apps generadas **no gestionan su propio cert**: corren con ingress interno y el TLS lo termina el
LB. Crear una app = dar de alta el `slug` en el `app-router`; el wildcard ya las cubre.

---

## 6. Compute (Cloud Run)

| Servicio | Rol | Escala/HA |
|---|---|---|
| **frontend** | SPA estática → **Firebase Hosting** (origen privado). | CDN global; sin servidores. |
| **builder-api** | FastAPI async (API, auth gateway, agente SSE, **connector-proxy**, proxy LLM). Expuesto en `api.hub.cashea.app` tras LB + Cloud Armor. | Cloud Run regional, `min-instances` para latencia, `max` para picos, multi-zona. |
| **worker** | Pipeline de deploy + QA. Consume Pub/Sub; usa Cloud Build + Cloud Run Admin API (SA de **deploy**, no de build). | Cloud Run / Cloud Run Jobs, autoescala por cola. |
| **app-router** | Enruta `*.hub` → Cloud Run de cada app. Stateless. | Autoescala. |
| **app-\<slug\>** (N) | Apps generadas (código no confiable). SA mínima, **sin VPC a la DB**, **egreso allowlisted**. | 1 servicio por app, scale-to-zero, autoescala. |

**Aislamiento — precisión técnica:** el aislamiento de Cloud Run depende de la *execution environment*
(1ª gen = sandbox gVisor; 2ª gen = microVM). Ambas dan aislamiento fuerte a nivel host; la garantía
concreta depende de cuál se fije por app — **a verificar contra la documentación vigente de Cloud Run**.

**SSE:** el run del agente sigue desacoplado (worker/coroutine → **Memorystore Redis Streams**); la
`builder-api` lee el stream y lo emite por SSE.

---

## 7. Datos y estado (gestionados, privados)

| Servicio | Uso | HA |
|---|---|---|
| **Cloud SQL (PostgreSQL)** | Base de la plataforma. **Solo IP privada**. | **HA regional** (failover automático), backups + **PITR**, **backups replicados cross-region**, read replicas. |
| **Memorystore (Redis)** | Streams/pub-sub del SSE (progreso de agente y deploy). | Tier **Standard (HA)**. |
| **Cloud Storage** | Versionado de código por app + adjuntos. | 11 nueves de durabilidad; buckets privados. |
| **Artifact Registry** | Imágenes base + por app. | Regional/multi-regional; **scan de vulnerabilidades** + base de Binary Authorization. |
| **Pub/Sub / Cloud Tasks** | Cola de jobs de deploy. | Gestionado, HA. |

**Continuidad (definir y aprobar):** **RPO ≤ 5 min** (PITR de Cloud SQL), **RTO ≤ 1 h** regional;
multi-región opcional baja el RTO geográfico. Backups **replicados a otra región** desde el día 1, con
**runbook de restauración probado**.

---

## 8. Secretos (Secret Manager)

- **Todo** secreto en **Secret Manager**: `ANTHROPIC/OPENAI/GEMINI`, OAuth (Google/Notion/Slack),
  **clave Fernet**, password de DB, y la **clave de derivación de `X-App-Secret`**.
- Cloud Run los monta en runtime (sin `.env` en imagen ni en repo).
- Tokens OAuth por-usuario **cifrados con Fernet** en Cloud SQL; la clave Fernet vive en Secret Manager.
- Acceso por **IAM granular**: solo la SA que lo necesita lee el secreto que necesita.

### 8.bis. `X-App-Secret` (secreto por-app del gateway)

- Usa una **clave dedicada** en Secret Manager (`APP_SECRET_KEY`), independiente del secreto de sesión
  de usuario: filtrar uno no compromete al otro.
- **Por app:** `X-App-Secret = HMAC(app_id, APP_SECRET_KEY)`; cada app conoce **solo el suyo**.
- **Rotación + revocación:** los secretos por-app se rotan y existe lista de revocación — un secreto
  comprometido se invalida sin esperar redeploy, y el gateway lo rechaza.

### Autenticación: Firebase Auth / Identity Platform

- Maneja **Google sign-in**, emite **ID token (JWT firmado por Google)** con **refresh**, **MFA**,
  expiración y gestión de cuentas — todo gestionado.
- `builder-api` **verifica** el ID token (Admin SDK / claves públicas de Google); no custodia un
  secreto de firmado de sesión → menos superficie.

---

## 9. Control de acceso: AuthN / AuthZ (Zero Trust)

### 9.1. Separación de planos

| Plano | Componente | Quién decide el acceso |
|---|---|---|
| **Control plane** | `builder-api` + Firebase Auth / Identity Platform | **Única fuente de verdad** de identidad y autorización (roles + allowlist por app). |
| **Data plane** | Apps generadas (`app-<slug>`) | **Delegan** la autorización en el control plane; no almacenan la lista de acceso. |

Una app comprometida **no puede ampliarse permisos**: la decisión vive en el control plane.

### 9.2. Autenticación (común a todo)

- Identidad federada (**Firebase Auth / Identity Platform**, Google + **MFA**).
- **ID token de vida corta**; toda request a `/api/*` lleva `Authorization: Bearer <ID token>`.
- Verificación **criptográfica** (firma + expiración); sin sesiones de servidor que robar.

### 9.3. Autorización — dos casos

- **App principal (Cashea Hub):** solo autenticado + **RBAC** (`admin`/`member`) + **ownership** del
  recurso (admins y compartidos = solo lectura sobre apps ajenas).
- **Apps secundarias:** autenticado **y** autorizado por recurso (**allowlist por app**,
  *deny-by-default*): entra **solo si** `email == owner_email` **OR** `email ∈ shared_emails`.

### 9.4. Doble control en apps secundarias

1. **AuthN** — middleware exige `Bearer` válido. Sin token → **401** → "Sign in".
2. **AuthZ** — la app **consulta al control plane** `GET /api/apps/{app_id}/access?email=…`
   autenticando con **`X-App-Secret`**. Si `false` → **403** → "No access".

Notas de seguridad:
- **Fuente de verdad = control plane:** quitar un correo de `shared_emails` **revoca en ≤ TTL** (caché
  ~30 s), sin redeploy.
- Las **decisiones de autorización se resuelven server-side** en el control plane (no solo por *custom
  claims*, que arrastran el lag de vida del token).
- **Cero datos sin autorización:** el shell estático puede ser público, pero todo dato sensible pasa
  por `/api/*` tras AuthN+AuthZ. El endpoint `/access` lleva **rate-limit** contra enumeración de
  `app_id`.
- **Trazabilidad:** cada allow/deny y cada invocación del **connector-proxy** (qué app, qué tool, qué
  conector) queda en **Cloud Audit Logs**.

### 9.5. Secuencia — acceso a una app secundaria

```
 Visitante        Firebase Auth      app-<slug>            builder-api (control plane)
 (navegador)      (Identity Plat.)   (data plane)          Cloud SQL: owner + shared_emails
     │                  │                  │                          │
     │ 1. abre app-<slug>.hub.cashea.app   │                          │
     │─────────────────────────────────────▶ (sirve shell estático, público, SIN datos)
     │ 2. ¿token? no → login                │                          │
     │─────────────────▶│  Google + MFA     │                          │
     │◀─────────────────│ 3. ID token (JWT firmado, vida corta)        │
     │ 4. GET /api/__whoami  Bearer <token> │                          │
     │─────────────────────────────────────▶│ AuthN: ¿firma+exp? no→401│
     │                  │        sí → 5. GET /access?email=… X-App-Secret│
     │                  │                       │──────────────────────▶│
     │                  │                       │ allowed = owner OR ∈shared
     │                  │                       │◀──────────────────────│
     │                  │   AuthZ: ¿allowed? no→403 ("No access")        │
     │◀───────────────────────────────────── sí→200, sirve datos /api   │
     │ 6. datos solo tras AuthN+AuthZ; terceros vía connector-proxy (§10)│
```

---

## 10. IAM, amenazas internas y aislamiento del tenant

- **Una SA por servicio, privilegio mínimo.** La **build SA** está **separada de la deploy SA**.
  - `builder-api`/`worker` (deploy): desplegar Cloud Run + leer secretos puntuales.
  - **build SA:** solo escribir en Artifact Registry; sin deploy ni secretos.
  - `app-<slug>`: casi sin permisos (no DB, no Secret Manager, no deploy).
- **Workload Identity** (sin archivos de llave de SA que se filtren). En CI/CD, **Workload Identity
  Federation para GitHub Actions** (sin llaves de larga vida).
- **Org Policies:** prohibir IP pública en VMs, restringir creación de keys de SA, *domain-restricted
  sharing*, regiones permitidas, **exigir Binary Authorization**.
- **Aislamiento del tenant (lo más crítico):**
  - Cada app es un **Cloud Run aislado** (gVisor/microVM según *exec env*), sin acceso al host.
  - SA mínima, **sin VPC connector a la DB** de la plataforma.
  - **Egreso allowlisted por FQDN** (Secure Web Proxy): restringe a dónde puede salir el código no
    confiable + logging + detección de anomalías.
  - Solo puede llamar al **gateway** de la plataforma a través del **connector-proxy (mecanismo
    único)**: la app pide "ejecuta esta tool de mi conector"; **la plataforma ejecuta la tool y
    devuelve solo el resultado**. **Nunca se entrega el token crudo a ninguna app**, en ningún caso.
- **Auditoría:** **Cloud Audit Logs** + **Security Command Center** + alertas en Monitoring, con
  **gobierno de PII en los logs** (retención + IAM sobre los logs).
- **Datos:** cifrado en reposo (**CMEK** con Cloud KMS) y en tránsito.

### 10.bis. Radio de impacto si comprometen una app

**No escala a la plataforma.** Una app comprometida queda en su sandbox:

| Vector de escalada | ¿Posible? | Por qué |
|---|---|---|
| Saltar al host / otras apps | **No** | Cloud Run aislado; no hay host compartido. |
| Leer la **DB** | **No** | SA sin IAM a Cloud SQL y **sin VPC connector**. |
| Leer **Secret Manager** | **No** | SA sin `secretmanager.access`. |
| Desplegar/alterar Cloud Run/Build/Registry | **No** | Esos roles los tienen solo deploy SA / build SA. |
| Escalar vía el **build** | **No** | Build con SA mínima sin deploy, sin red; solo despliegan imágenes firmadas (Binary Authz). |
| Exfiltrar por egreso libre | **No** | **Egreso allowlisted por FQDN** (Secure Web Proxy) + logging. |
| Hacerse pasar por otra app | **No** | El gateway valida `X-App-Secret == HMAC(app_id, APP_SECRET_KEY)`; rotación + revocación. |
| **Robar el token de un conector** | **No** | El **connector-proxy** ejecuta la tool del lado de la plataforma; **el token crudo nunca sale hacia la app**. |

**Lo que sí queda en riesgo (acotado):** mientras la app está comprometida, puede pedir al
**connector-proxy** que **ejecute tools de los conectores de SU dueño** (dentro de los scopes
concedidos) y leer **resultados**. Pero **nunca obtiene el token crudo**, así que el abuso es
*efímero* (vive solo mientras la app está comprometida y autenticada ante el gateway) y **no hay
credencial robable que persista**: revocada/rotada la app, el acceso a los conectores se corta. El
compromiso no toca otras apps, otros dueños, ni la plataforma.

**Mitigaciones del riesgo residual:**
- **Scopes mínimos** por conector (solo lo que la app necesita) y **políticas por tool** en el proxy
  (qué operaciones se permiten, no solo qué API).
- **Rate-limits y cuotas por app** en el connector-proxy; **detección de anomalías** (picos de
  invocaciones, patrones inusuales) en SCC/Monitoring.
- **Tokens de los conectores de vida corta + refresh** custodiados por la plataforma (nunca por la app).
- **Revocación inmediata**: invalidar el `X-App-Secret` de la app corta su acceso al proxy sin redeploy.

---

## 11. Alta disponibilidad, escalabilidad y ciclo de vida

- **Cloud Run:** multi-zona; autoescala 0→N; `min-instances` para evitar cold starts en `builder-api`.
  Opcional **multi-región** con el LB global.
- **Cloud SQL HA** regional + read replicas; **Memorystore Standard** (réplica + failover).
- **CDN** (Cloudflare + Cloud CDN) para estáticos.
- **Escala del modelo 1-Cloud-Run-por-app:** vigilar cuotas de servicios Cloud Run por proyecto/región,
  concurrencia de Cloud Build y crecimiento de Artifact Registry. Mitigaciones: **caché de capas en
  build**, base images propias, y **ciclo de vida / GC de apps** (suspender o borrar apps inactivas).
  `scale-to-zero` baja el costo a cambio de cold starts.
- **Control plane:** cada request de cada app consulta `builder-api` para AuthZ. Diseño **fail-closed**
  (si el control plane cae, las apps niegan acceso); la **caché ~30 s** amortigua la dependencia.
  `builder-api` con `min-instances` y multi-zona para sostener su rol de dependencia dura.

---

## 12. Observabilidad y CI/CD

- **Cloud Logging / Monitoring / Trace / Error Reporting:** logs centralizados, métricas, latencias,
  uptime checks, **SLOs y alertas**. **Gobierno de PII** en los logs (retención + acceso restringido).
- **CI/CD:** GitHub → **Cloud Build** (build/test, **WIF sin llaves**) → Artifact Registry (**scan**) →
  **firma + Binary Authorization** → `gcloud run deploy` / **Cloud Deploy** con despliegues
  **canary/gradual** y rollback. La plataforma y las apps usan el mismo pipeline endurecido.

---

## 13. Defensa en profundidad (resumen)

**Desde afuera:** Cloudflare (WAF/DDoS/bot, **AOP mTLS custom**) → Cloud Armor (OWASP/rate/Adaptive,
incl. `/api`) → LB con TLS → origen bloqueado a IPs de CF → Cloud Run con ingress controlado → IAP en
lo interno.

**Identidad/acceso:** Firebase Auth/Identity Platform (Google + MFA, tokens cortos) → toda request a
datos pasa por AuthN + AuthZ resuelta **server-side en el control plane** → RBAC + ownership →
allowlist *deny-by-default* → revocación en ≤ TTL sin redeploy.

**Desde adentro:** SAs mínimas (**build SA ≠ deploy SA**) + Workload Identity (sin keys) → tenant apps
en sandbox **sin DB ni secretos** y con **egreso allowlisted** → **build endurecido + Binary
Authorization** → VPC privada + VPC-SC (anti-exfiltración) → Secret Manager con IAM granular + rotación
→ Cloud Audit Logs + SCC → sin SSH.

---

## 14. Fases de implementación (incremental, sin downtime)

1. **Datos:** Cloud SQL HA + Memorystore; migrar la DB (DMS); secretos a Secret Manager; backups
   cross-region.
2. **Plataforma:** `builder-api`/`frontend`/`worker` en Cloud Run tras LB + Cloud Armor + Cloudflare;
   `api.hub.cashea.app` tras WAF; broker en Pub/Sub.
3. **Apps:** **Cloud Build (build SA mínima) + Cloud Run Admin API** (deploy por app) + router `*.hub`;
   activar **egreso allowlisted** y el **connector-proxy como mecanismo único** (sin entrega de tokens
   crudos a las apps).
4. **Endurecer:** **Binary Authorization**, VPC-SC, Org Policies, IAP, CMEK, scanning, SCC,
   alertas/SLOs, **WIF para GitHub**, gobierno de PII en logs, **rotación de `X-App-Secret`**.
5. (Opcional) **multi-región** para HA geográfica.

---

## 15. Checklist de aprobación

- [ ] **Build SA separada de deploy SA**, build sin red, sin scripts arbitrarios.
- [ ] **Binary Authorization** activo (solo imágenes firmadas a Cloud Run).
- [ ] **Egreso allowlisted por FQDN** para tenant apps (Secure Web Proxy).
- [ ] **connector-proxy como mecanismo único**: ninguna app recibe jamás un token crudo de conector.
- [ ] **`/api` tras LB + Cloud Armor** (`api.hub.cashea.app`).
- [ ] **AOP con certificado custom** + allowlist de IPs de Cloudflare.
- [ ] **`X-App-Secret` con clave propia + rotación + revocación**.
- [ ] **Backups cross-region** y **RTO/RPO aprobados**; runbook de restore probado.
- [ ] **WIF para GitHub Actions** (sin llaves de larga vida).
- [ ] **Gobierno de PII en logs** (retención + IAM).
- [ ] *Execution environment* de Cloud Run de tenants verificada contra la doc vigente.
