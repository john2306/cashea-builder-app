import { useCallback, useEffect, useState } from "react";
import { getToken } from "../lib/auth";

interface McpConn {
  provider: string;
  label: string;
  brand: string;
  transport: "hosted" | "self_hosted" | "api";
  auth: "oauth" | "env" | "none";
  connected: boolean;
  needs_env: string[];
  workspace_only?: boolean;
}

function NotionLogo() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path
        d="M4 4.6 14.3 3.9c1.3-.1 1.6 0 2.4.6l2.6 1.8c.5.4.7.5.7 1v12.3c0 .8-.3 1.3-1.4 1.4l-11.4.7c-.8 0-1.2-.1-1.6-.6L3.9 19c-.4-.5-.6-.9-.6-1.4V5.7C3.3 5.1 3.5 4.7 4 4.6Z"
        fill="#fff"
        stroke="#191919"
        strokeWidth="1.1"
      />
      <path
        d="M8.4 8v7.4M8.4 8l5.3 7.2M8.4 8 7 7.9m6.7 7.3V8.2m0 0 1.3-.1"
        fill="none"
        stroke="#191919"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SheetsLogo() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-7-7Z" fill="#0f9d58" />
      <path d="M13 2v5a2 2 0 0 0 2 2h5" fill="#0c8043" />
      <path d="M8 12.5h8M8 15.5h8M8 18h8M11 12v6.5M14 12v6.5" stroke="#fff" strokeWidth="1.1" />
    </svg>
  );
}

function DriveLogo() {
  return (
    <svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true">
      <path d="M12 2.5 5.33 13.5H12Z" fill="#00ac47" />
      <path d="M12 2.5V13.5h6.67Z" fill="#ffba00" />
      <path d="M5.33 13.5h13.34L22 19H2Z" fill="#2684fc" />
    </svg>
  );
}

function DocsLogo() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-7-7Z" fill="#4285f4" />
      <path d="M13 2v5a2 2 0 0 0 2 2h5" fill="#a1c2fa" />
      <path d="M8 12.5h8M8 15h8M8 17.5h5" stroke="#fff" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  );
}

function SlackLogo() {
  return (
    <svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true">
      <path d="M6 14.5a2 2 0 1 1-2-2h2v2Zm1 0a2 2 0 1 1 4 0v5a2 2 0 1 1-4 0v-5Z" fill="#e01e5a" />
      <path d="M9.5 6a2 2 0 1 1 2-2v2h-2Zm0 1a2 2 0 1 1 0 4h-5a2 2 0 1 1 0-4h5Z" fill="#36c5f0" />
      <path d="M18 9.5a2 2 0 1 1 2 2h-2v-2Zm-1 0a2 2 0 1 1-4 0v-5a2 2 0 1 1 4 0v5Z" fill="#2eb67d" />
      <path d="M14.5 18a2 2 0 1 1-2 2v-2h2Zm0-1a2 2 0 1 1 0-4h5a2 2 0 1 1 0 4h-5Z" fill="#ecb22e" />
    </svg>
  );
}

function CalendarLogo() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <rect x="4.5" y="5" width="15" height="15" rx="2.5" fill="#fff" stroke="#dadce0" strokeWidth="1" />
      <path d="M4.5 7.5a2.5 2.5 0 0 1 2.5-2.5h10a2.5 2.5 0 0 1 2.5 2.5V9h-15V7.5Z" fill="#4285f4" />
      <circle cx="8" cy="4.3" r="1.05" fill="#9aa0a6" />
      <circle cx="16" cy="4.3" r="1.05" fill="#9aa0a6" />
      <text x="12" y="17.6" textAnchor="middle" fontSize="8.5" fontWeight="700" fill="#4285f4" fontFamily="Arial, sans-serif">31</text>
    </svg>
  );
}

function BigQueryLogo() {
  return (
    <svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true">
      <path d="M12 2 20.66 7V17L12 22 3.34 17V7Z" fill="#4285f4" />
      <circle cx="10.4" cy="10.4" r="3.9" fill="none" stroke="#fff" strokeWidth="1.5" />
      <rect x="8.55" y="9.5" width="1.05" height="2.5" rx="0.35" fill="#fff" />
      <rect x="9.95" y="8.5" width="1.05" height="3.5" rx="0.35" fill="#fff" />
      <rect x="11.35" y="10.1" width="1.05" height="1.9" rx="0.35" fill="#fff" />
      <path d="M13.5 13.5 16.7 16.7" stroke="#fff" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function CloudStorageLogo() {
  return (
    <svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true">
      <path d="M12 2 20.66 7V17L12 22 3.34 17V7Z" fill="#4285f4" />
      <rect x="6.6" y="9" width="10.8" height="2.7" rx="1" fill="#fff" />
      <rect x="6.6" y="12.6" width="10.8" height="2.7" rx="1" fill="#fff" />
      <circle cx="15.1" cy="10.35" r="0.62" fill="#4285f4" />
      <circle cx="15.1" cy="13.95" r="0.62" fill="#4285f4" />
    </svg>
  );
}

function GmailLogo() {
  return (
    <svg viewBox="0 0 256 193" width="23" height="23" aria-hidden="true">
      <path fill="#4285f4" d="M58.18 192.05V93.14L27.5 65.08 0 49.5v125.1c0 9.66 7.82 17.45 17.45 17.45z" />
      <path fill="#34a853" d="M197.82 192.05h40.73c9.66 0 17.45-7.83 17.45-17.45V49.5l-31.16 17.84-27.02 25.8z" />
      <path fill="#ea4335" d="m58.18 93.14-4.17-38.65 4.17-36.99L128 69.87l69.82-52.37 4.67 34.99-4.67 40.65L128 145.5z" />
      <path fill="#fbbc04" d="M197.82 17.5v75.64L256 49.5V26.23c0-21.59-24.64-33.89-41.89-20.94z" />
      <path fill="#c5221f" d="M0 49.5 26.76 69.57l31.42 23.57V17.5L41.89 5.29C24.61-7.66 0 4.65 0 26.23z" />
    </svg>
  );
}

function MiroLogo() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#ffd02f" />
      <text
        x="12"
        y="16.3"
        textAnchor="middle"
        fontSize="8.2"
        fontWeight="800"
        fill="#050038"
        fontFamily="Arial, sans-serif"
      >
        miro
      </text>
    </svg>
  );
}

function IntercomLogo() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <rect x="2" y="2" width="20" height="20" rx="4.5" fill="#000" />
      <g fill="#fff">
        <rect x="5.7" y="8.4" width="1.5" height="5.4" rx="0.75" />
        <rect x="8.55" y="6.9" width="1.5" height="7.9" rx="0.75" />
        <rect x="11.25" y="6.5" width="1.5" height="8.5" rx="0.75" />
        <rect x="13.95" y="6.9" width="1.5" height="7.9" rx="0.75" />
        <rect x="16.8" y="8.4" width="1.5" height="5.4" rx="0.75" />
      </g>
      <path
        d="M6.4 16.3c3.4 2.6 7.8 2.6 11.2 0"
        fill="none"
        stroke="#fff"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

const LOGOS: Record<string, () => JSX.Element> = {
  notion: NotionLogo,
  google_sheets: SheetsLogo,
  google_docs: DocsLogo,
  google_drive: DriveLogo,
  slack: SlackLogo,
  google_calendar: CalendarLogo,
  bigquery: BigQueryLogo,
  cloud_storage: CloudStorageLogo,
  intercom: IntercomLogo,
  gmail: GmailLogo,
  miro: MiroLogo,
};

export function McpView() {
  const [items, setItems] = useState<McpConn[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");

  const refresh = useCallback(() => {
    fetch("/api/mcp/connections")
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setItems(d))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const h = (e: MessageEvent) => {
      if (e.data === "connector:refresh") refresh();
    };
    window.addEventListener("message", h);
    return () => window.removeEventListener("message", h);
  }, [refresh]);

  const connect = (p: string) =>
    // El popup OAuth es navegación del navegador (no lleva el header Bearer): pasamos el
    // token por query para que el backend sepa qué usuario está conectando (bitácora).
    window.open(
      `/api/mcp/${p}/connect?token=${encodeURIComponent(getToken() ?? "")}`,
      "_blank",
      "width=560,height=720",
    );
  const disconnect = (p: string) =>
    fetch(`/api/mcp/${p}`, { method: "DELETE" }).then(refresh);

  // Orden alfabético por nombre + filtro por texto (nombre del MCP).
  const visible = items
    .filter((it) => it.label.toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a, b) => a.label.localeCompare(b.label, "es", { sensitivity: "base" }));

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Conectores del agente</p>
          <h1>Connectors</h1>
        </div>
        <input
          className="logs-search mcp-search"
          type="search"
          placeholder="Buscar conector…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </header>

      {loading ? (
        <p className="muted-note">Cargando…</p>
      ) : visible.length === 0 ? (
        <p className="muted-note">No hay conectores que coincidan con «{q}».</p>
      ) : (
        <div className="card-grid">
          {visible.map((it) => {
            const Logo = LOGOS[it.provider];
            const isOAuth = it.auth === "oauth";
            const stateLabel = isOAuth
              ? it.connected
                ? "Conectado"
                : "Sin conectar"
              : it.connected
                ? "Listo"
                : "Requiere credenciales";
            return (
              <article className={`mcp-card ${it.connected ? "connected" : ""}`} key={it.provider}>
                <div className="mcp-card-head">
                  <span className="mcp-card-icon" aria-hidden="true">
                    {Logo ? (
                      <Logo />
                    ) : (
                      <span
                        className="mcp-mono"
                        style={{ background: it.brand }}
                      >
                        {it.label.charAt(0)}
                      </span>
                    )}
                  </span>
                  <div className="mcp-card-meta">
                    <h3>{it.label}</h3>
                    <span className={`mcp-state ${it.connected ? "on" : "off"}`}>{stateLabel}</span>
                  </div>
                  <span
                    className={`mcp-badge ${it.transport === "api" ? "api" : "mcp"}`}
                    title={
                      it.transport === "api"
                        ? "Integración vía API directa"
                        : "Integración vía servidor MCP"
                    }
                  >
                    {it.transport === "api" ? "API" : "MCP"}
                  </span>
                </div>

                {isOAuth ? (
                  it.connected ? (
                    <button
                      className="btn ghost"
                      type="button"
                      onClick={() => disconnect(it.provider)}
                    >
                      Desconectar
                    </button>
                  ) : (
                    <button
                      className="btn primary"
                      type="button"
                      onClick={() => connect(it.provider)}
                    >
                      Conectar
                    </button>
                  )
                ) : (
                  <p className="mcp-hint">
                    {it.connected
                      ? "Contenedor activo con credenciales."
                      : `Define en .env: ${it.needs_env.join(", ")}`}
                  </p>
                )}

                {it.workspace_only && (
                  <p className="mcp-ws-note">Solo con Google Workspace</p>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
