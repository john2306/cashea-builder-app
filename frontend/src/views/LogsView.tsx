import { useCallback, useEffect, useState } from "react";
import { Dropdown } from "../components/Dropdown";
import { DateRangeField } from "../components/DateRangeField";

interface LogItem {
  id: string;
  type: string;
  status: string;
  user: string | null;
  app_id: string | null;
  provider: string | null;
  message: string | null;
  meta: Record<string, unknown> | null;
  created_at: string | null;
}
interface Facets {
  types: string[];
  statuses: string[];
  users: string[];
}

const PAGE = 50;

const TYPE_LABEL: Record<string, string> = {
  "app.create": "App creada",
  "app.update": "App actualizada",
  "app.delete": "App eliminada",
  "app.define": "App definida",
  "app.edit": "Cambio solicitado",
  "agent.trace": "Builder agent · trace",
  "deploy.start": "Deploy iniciado",
  "deploy.done": "Deploy OK",
  "deploy.error": "Deploy con error",
  "deploy.rollback": "Rollback",
  "mcp.connect": "MCP conectado",
  "mcp.disconnect": "MCP desconectado",
  "llm.call": "LLM · llamada",
  "connector.call": "Conector · llamada",
  "auth.login": "Inicio de sesión",
};

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function LogsView() {
  const [items, setItems] = useState<LogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<Facets>({ types: [], statuses: [], users: [] });
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // filtros
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    fetch("/api/logs/facets")
      .then((r) => (r.ok ? r.json() : { types: [], statuses: [], users: [] }))
      .then(setFacets)
      .catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    const p = new URLSearchParams();
    if (type) p.set("type", type);
    if (status) p.set("status", status);
    if (user) p.set("user", user);
    if (from) p.set("from", from);
    if (to) p.set("to", to);
    if (q.trim()) p.set("q", q.trim());
    p.set("limit", String(PAGE));
    p.set("offset", String(page * PAGE));
    fetch(`/api/logs?${p.toString()}`)
      .then((r) => {
        if (r.status === 403) {
          setForbidden(true);
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then((d) => {
        if (d) {
          setItems(d.items);
          setTotal(d.total);
        }
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [type, status, user, from, to, q, page]);

  useEffect(() => {
    load();
  }, [load]);

  // Cambiar un filtro vuelve a la primera página.
  const onFilter = (setter: (v: string) => void) => (v: string) => {
    setPage(0);
    setter(v);
  };
  const onRange = (f: string, t: string) => {
    setPage(0);
    setFrom(f);
    setTo(t);
  };
  const clearFilters = () => {
    setPage(0);
    setType("");
    setStatus("");
    setUser("");
    setFrom("");
    setTo("");
    setQ("");
  };

  const pages = Math.ceil(total / PAGE) || 1;

  if (forbidden) {
    return (
      <div className="page">
        <header className="page-head">
          <h1>Logs</h1>
        </header>
        <p className="muted-note">No tienes permiso para ver la bitácora (solo administradores).</p>
      </div>
    );
  }

  return (
    <div className="page logs-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Auditoría · Admin</p>
          <h1>Logs</h1>
        </div>
      </header>

      <div className="logs-filters">
        <Dropdown
          value={type}
          onChange={onFilter(setType)}
          placeholder="Todos los tipos"
          options={[
            { value: "", label: "Todos los tipos" },
            ...facets.types.map((t) => ({ value: t, label: TYPE_LABEL[t] ?? t })),
          ]}
        />
        <Dropdown
          value={status}
          onChange={onFilter(setStatus)}
          placeholder="Todos los estados"
          options={[
            { value: "", label: "Todos los estados" },
            ...facets.statuses.map((s) => ({ value: s, label: s })),
          ]}
        />
        <Dropdown
          value={user}
          onChange={onFilter(setUser)}
          placeholder="Todos los usuarios"
          options={[
            { value: "", label: "Todos los usuarios" },
            ...facets.users.map((u) => ({ value: u, label: u })),
          ]}
        />
        <DateRangeField from={from} to={to} onChange={onRange} />
        <input
          className="logs-search"
          placeholder="Buscar en el mensaje…"
          value={q}
          onChange={(e) => onFilter(setQ)(e.target.value)}
        />
        <button className="btn ghost sm" type="button" onClick={clearFilters}>
          Limpiar
        </button>
      </div>

      <div className="logs-table">
        <div className="logs-row logs-row-head">
          <span>Fecha</span>
          <span>Evento</span>
          <span>Estado</span>
          <span>Usuario</span>
          <span>Detalle</span>
        </div>
        {loading ? (
          <p className="muted-note">Cargando…</p>
        ) : items.length === 0 ? (
          <p className="muted-note">No hay eventos para los filtros seleccionados.</p>
        ) : (
          items.map((it) => {
            const open = expandedId === it.id;
            return (
              <div className={`logs-entry ${open ? "open" : ""}`} key={it.id}>
                <div
                  className="logs-row logs-row-click"
                  role="button"
                  tabIndex={0}
                  onClick={() => setExpandedId(open ? null : it.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setExpandedId(open ? null : it.id);
                    }
                  }}
                >
                  <span className="logs-date-cell">
                    <span className={`logs-caret ${open ? "up" : ""}`} aria-hidden="true" />
                    {fmt(it.created_at)}
                  </span>
                  <span className="logs-type">{TYPE_LABEL[it.type] ?? it.type}</span>
                  <span className={`logs-status ${it.status}`}>{it.status}</span>
                  <span className="logs-user">{it.user ?? "—"}</span>
                  <span className="logs-msg">{it.message ?? (it.provider ? it.provider : "—")}</span>
                </div>
                {open && (
                  <div className="logs-detail">
                    <div className="logs-detail-grid">
                      <span className="logs-detail-k">Evento</span>
                      <span className="logs-detail-v">{TYPE_LABEL[it.type] ?? it.type} ({it.type})</span>
                      <span className="logs-detail-k">Estado</span>
                      <span className="logs-detail-v">
                        <span className={`logs-status ${it.status}`}>{it.status}</span>
                      </span>
                      <span className="logs-detail-k">Fecha</span>
                      <span className="logs-detail-v">{fmt(it.created_at)}</span>
                      <span className="logs-detail-k">Usuario</span>
                      <span className="logs-detail-v">{it.user ?? "—"}</span>
                      {it.provider && (
                        <>
                          <span className="logs-detail-k">Proveedor</span>
                          <span className="logs-detail-v">{it.provider}</span>
                        </>
                      )}
                      {it.app_id && (
                        <>
                          <span className="logs-detail-k">App</span>
                          <span className="logs-detail-v mono">{it.app_id}</span>
                        </>
                      )}
                    </div>
                    {it.message && (
                      <pre className={`logs-detail-msg ${it.status === "error" ? "error" : ""}`}>
                        {it.message}
                      </pre>
                    )}
                    {it.meta && Object.keys(it.meta).length > 0 && (
                      <pre className="logs-detail-meta">{JSON.stringify(it.meta, null, 2)}</pre>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <div className="logs-foot">
        <span className="muted-note">
          {total} evento{total === 1 ? "" : "s"}
        </span>
        <div className="logs-pager">
          <button
            className="btn ghost sm"
            type="button"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            ← Anterior
          </button>
          <span className="muted-note">
            {page + 1} / {pages}
          </span>
          <button
            className="btn ghost sm"
            type="button"
            disabled={page + 1 >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            Siguiente →
          </button>
        </div>
      </div>
    </div>
  );
}
