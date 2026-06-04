import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DeployDialog } from "../components/DeployDialog";
import { ShareDialog } from "../components/ShareDialog";
import { SpecReviewDialog } from "../components/SpecReviewDialog";
import { VersionsDialog } from "../components/VersionsDialog";
import { Dropdown } from "../components/Dropdown";
import { getToken } from "../lib/auth";
import type { AppProject } from "../types";

const DEPLOY_LABEL: Record<string, string> = {
  idle: "Not deployed",
  deploying: "Deploying…",
  deployed: "Live",
  error: "Error",
};

type Sort = "new" | "old" | "az" | "za" | "live";
const SORTS: { id: Sort; label: string }[] = [
  { id: "new", label: "Newest" },
  { id: "old", label: "Oldest" },
  { id: "az", label: "Name A → Z" },
  { id: "za", label: "Name Z → A" },
  { id: "live", label: "Live first" },
];

const PAGE = 30; // tamaño de página (paginación server-side para escalar a miles de apps)

const EMOJIS = [
  "📊", "📈", "📉", "🗓️", "📅", "⏰", "📨", "✉️",
  "🤖", "🛒", "💳", "💰", "🪙", "🏦", "👥", "👤",
  "🔔", "📦", "🧾", "📁", "📂", "🗂️", "✅", "☑️",
  "🌐", "⚙️", "🛠️", "🚀", "⭐", "🎯", "📌", "🏷️",
  "💡", "🔒", "🔑", "📡", "🧩", "🧠", "💬", "📞",
  "📤", "📥", "🛍️", "📍", "🔎", "🗒️", "📋", "🔥",
];
const COLORS = [
  "#3b82f6", "#06b6d4", "#10b981", "#22c55e",
  "#84cc16", "#eab308", "#f59e0b", "#f97316",
  "#ef4444", "#ec4899", "#d946ef", "#8b5cf6",
  "#6366f1", "#14b8a6", "#64748b", "#0f172a",
];

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 20h4l10-10-4-4L4 16v4z" />
      <path d="M14 6l4 4" />
    </svg>
  );
}


export function AppsView({
  onBuild,
  isAdmin = false,
}: {
  onBuild: (appId: string) => void;
  isAdmin?: boolean;
}) {
  const [apps, setApps] = useState<AppProject[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [ownerFilter, setOwnerFilter] = useState(""); // "" = todos (solo admin)
  const [owners, setOwners] = useState<string[]>([]); // dueños para el filtro admin
  const [scope, setScope] = useState(""); // member: "" ambas | mine | shared
  const [pendingDelete, setPendingDelete] = useState<AppProject | null>(null);
  const [pendingShare, setPendingShare] = useState<AppProject | null>(null);
  const [pendingDeploy, setPendingDeploy] = useState<AppProject | null>(null);
  const [pendingReview, setPendingReview] = useState<AppProject | null>(null);
  const [pendingVersions, setPendingVersions] = useState<AppProject | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("new");
  const [sortOpen, setSortOpen] = useState(false);
  const [menu, setMenu] = useState<{ app: AppProject; top: number; right: number } | null>(null);
  const [picker, setPicker] = useState<{ app: AppProject; top: number; left: number } | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // URL de la lista con paginación + búsqueda/orden/filtro server-side (escala a miles de apps).
  const listUrl = useCallback(
    (offset: number, limit: number) => {
      const p = new URLSearchParams({ limit: String(limit), offset: String(offset), sort });
      if (query.trim()) p.set("q", query.trim());
      if (isAdmin && ownerFilter) p.set("owner", ownerFilter);
      if (!isAdmin && scope) p.set("scope", scope);
      return `/api/apps?${p.toString()}`;
    },
    [query, sort, isAdmin, ownerFilter, scope],
  );

  // Primera página: reemplaza la lista al cambiar búsqueda/orden/filtro.
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(listUrl(0, PAGE));
      if (r.ok) {
        const d = await r.json();
        setApps(d.items ?? []);
        setTotal(d.total ?? 0);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [listUrl]);

  // Re-trae los ya cargados (sin perder la cantidad) para refrescar estados de deploy.
  const refresh = useCallback(async () => {
    try {
      const r = await fetch(listUrl(0, Math.max(PAGE, apps.length)));
      if (r.ok) {
        const d = await r.json();
        setApps(d.items ?? []);
        setTotal(d.total ?? 0);
      }
    } catch {
      /* ignore */
    }
  }, [listUrl, apps.length]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const r = await fetch(listUrl(apps.length, PAGE));
      if (r.ok) {
        const d = await r.json();
        setApps((prev) => [...prev, ...(d.items ?? [])]);
        setTotal(d.total ?? 0);
      }
    } catch {
      /* ignore */
    } finally {
      setLoadingMore(false);
    }
  }, [listUrl, apps.length]);

  // (Re)carga la 1ra página al cambiar filtros; debounce para no consultar en cada tecla.
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  // Dueños para el filtro de admin (todos los usuarios conocidos).
  useEffect(() => {
    if (!isAdmin) return;
    fetch("/api/users")
      .then((r) => (r.ok ? r.json() : []))
      .then((us: { email: string }[]) => setOwners(us.map((u) => u.email).sort()))
      .catch(() => {});
  }, [isAdmin]);

  useEffect(() => {
    const anyDeploying = apps.some((a) => a.deploy_state === "deploying");
    if (anyDeploying && !timer.current) {
      timer.current = setInterval(refresh, 1500);
    } else if (!anyDeploying && timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
    return () => {
      if (timer.current) {
        clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [apps, refresh]);

  // Progreso del deploy en tiempo real (SSE + Redis pub/sub) por cada app desplegando.
  const esMap = useRef<Record<string, EventSource>>({});
  useEffect(() => {
    const deploying = new Set(apps.filter((a) => a.deploy_state === "deploying").map((a) => a.id));
    deploying.forEach((id) => {
      if (esMap.current[id]) return;
      const es = new EventSource(
        `/api/apps/${id}/deploy/stream?token=${encodeURIComponent(getToken() ?? "")}`,
      );
      es.onmessage = (e) => {
        let evt: { type: string; text?: string };
        try {
          evt = JSON.parse(e.data);
        } catch {
          return;
        }
        if (evt.type === "stage") {
          setApps((prev) =>
            prev.map((a) => (a.id === id ? { ...a, deploy_stage: evt.text ?? a.deploy_stage } : a)),
          );
        } else if (["done", "error", "cancelled"].includes(evt.type)) {
          es.close();
          delete esMap.current[id];
          refresh();
        }
      };
      esMap.current[id] = es;
    });
    Object.keys(esMap.current).forEach((id) => {
      if (!deploying.has(id)) {
        esMap.current[id].close();
        delete esMap.current[id];
      }
    });
  }, [apps, refresh]);

  useEffect(() => {
    const map = esMap.current;
    return () => Object.values(map).forEach((es) => es.close());
  }, []);

  const cancelDeploy = (id: string) => {
    fetch(`/api/apps/${id}/deploy/cancel`, { method: "POST" }).then(refresh);
  };

  // Permisos por app (el backend manda `my_role`). canEdit: desplegar/editar; canOwn: eliminar/compartir.
  const roleOf = (a: AppProject) => a.my_role || (isAdmin ? "admin" : "viewer");
  const canEdit = (a: AppProject) => ["admin", "owner", "editor"].includes(roleOf(a));
  const canOwn = (a: AppProject) => ["admin", "owner"].includes(roleOf(a));

  const createAndBuild = async () => {
    const r = await fetch("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "New agent" }),
    });
    if (r.ok) onBuild((await r.json()).id);
  };

  const confirmRemove = () => {
    if (!pendingDelete) return;
    const id = pendingDelete.id;
    setPendingDelete(null);
    fetch(`/api/apps/${id}`, { method: "DELETE" }).then(refresh);
  };

  // Actualiza la app (ícono/color/nombre): optimista + PATCH; revierte con refresh si falla.
  const patchApp = useCallback(
    (id: string, body: Partial<Pick<AppProject, "title" | "icon" | "color">>) => {
      setApps((prev) => prev.map((a) => (a.id === id ? { ...a, ...body } : a)));
      setPicker((p) => (p && p.app.id === id ? { ...p, app: { ...p.app, ...body } } : p));
      fetch(`/api/apps/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => refresh());
    },
    [refresh],
  );

  const startEdit = (app: AppProject) => {
    setNameDraft(app.title);
    setEditingId(app.id);
  };
  const saveName = (app: AppProject) => {
    const t = nameDraft.trim();
    setEditingId(null);
    if (t && t !== app.title) patchApp(app.id, { title: t });
  };

  return (
    <div className="page hub">
      <header className="hub-head">
        <div>
          <h1>Apps</h1>
          <p className="hub-count">{total} app{total === 1 ? "" : "s"}</p>
        </div>
        <div className="hub-tools">
          <div className="hub-search">
            <span className="hub-search-icon" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search apps…"
            />
          </div>
          {isAdmin ? (
            <Dropdown
              className="hub-owner-filter"
              value={ownerFilter}
              onChange={setOwnerFilter}
              placeholder="All users"
              options={[
                { value: "", label: "All users" },
                ...owners.map((o) => ({ value: o, label: o })),
              ]}
            />
          ) : (
            <Dropdown
              className="hub-owner-filter"
              value={scope}
              onChange={setScope}
              options={[
                { value: "", label: "All" },
                { value: "mine", label: "My apps" },
                { value: "shared", label: "Shared with me" },
              ]}
            />
          )}
          <div className="hub-sort-dd">
            <button
              className="hub-sort"
              type="button"
              onClick={() => setSortOpen((v) => !v)}
              aria-haspopup="listbox"
              aria-expanded={sortOpen}
            >
              <span className="hub-sort-icon" aria-hidden="true">⇅</span>
              {SORTS.find((s) => s.id === sort)?.label}
              <span className={`chevron ${sortOpen ? "up" : ""}`} aria-hidden="true" />
            </button>
            {sortOpen && (
              <>
                <div className="hub-menu-backdrop" onClick={() => setSortOpen(false)} />
                <div className="hub-sort-menu" role="listbox">
                  {SORTS.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      role="option"
                      aria-selected={s.id === sort}
                      className={s.id === sort ? "selected" : ""}
                      onClick={() => {
                        setSort(s.id);
                        setSortOpen(false);
                      }}
                    >
                      {s.label}
                      {s.id === sort && <span className="hub-sort-dot" aria-hidden="true" />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <button className="hub-new" type="button" onClick={createAndBuild}>
            + New app
          </button>
        </div>
      </header>

      {loading ? (
        <p className="muted-note">Loading…</p>
      ) : apps.length === 0 ? (
        <p className="muted-note">
          {query.trim() || ownerFilter ? "No matching apps." : 'No apps yet. Create one with "New app".'}
        </p>
      ) : (
        <div className="hub-table">
          <div className="hub-row hub-row-head">
            <span>App</span>
            <span>Status</span>
            <span>Updated</span>
            <span />
          </div>

          {apps.map((app) => {
            const live = app.deploy_state === "deployed";
            const sharedWithMe = app.my_role === "editor" || app.my_role === "viewer";
            return (
              <div className="hub-row" key={app.id}>
                <div className="hub-name">
                  <button
                    className="hub-icon-btn"
                    type="button"
                    style={app.color ? { background: app.color, color: "#fff" } : undefined}
                    title="Icon and color"
                    aria-label="Choose icon and color"
                    onClick={(e) => {
                      const r = e.currentTarget.getBoundingClientRect();
                      setPicker({ app, top: r.bottom + 8, left: r.left });
                    }}
                  >
                    {app.icon ? (
                      <span className="hub-emoji">{app.icon}</span>
                    ) : (
                      <span className="hub-initial">{app.title.charAt(0).toUpperCase()}</span>
                    )}
                  </button>
                  {editingId === app.id ? (
                    <input
                      className="hub-name-input"
                      value={nameDraft}
                      autoFocus
                      maxLength={120}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onBlur={() => saveName(app)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          saveName(app);
                        }
                        if (e.key === "Escape") setEditingId(null);
                      }}
                    />
                  ) : (
                    <div className="hub-name-main">
                      <span className="hub-title">
                        <span className="hub-title-text">{app.title}</span>
                        {canEdit(app) && (
                          <button
                            className="hub-name-edit"
                            type="button"
                            aria-label="Edit name"
                            onClick={() => startEdit(app)}
                          >
                            <PencilIcon />
                          </button>
                        )}
                        {sharedWithMe && (
                          <span
                            className="hub-shared-tag"
                            title={`Shared by ${app.owner_email ?? "another user"}`}
                          >
                            Shared
                          </span>
                        )}
                      </span>
                      {(isAdmin || sharedWithMe) && (app.owner_email || sharedWithMe) && (
                        <span
                          className="hub-owner"
                          title={app.owner_email ? `Owner: ${app.owner_email}` : undefined}
                        >
                          {app.owner_email ?? "Unknown owner"}
                          {sharedWithMe && (
                            <> · {app.my_role === "editor" ? "Edit" : "View"}</>
                          )}
                        </span>
                      )}
                    </div>
                  )}
                </div>

                <span
                  className={`hub-pill ${live ? "live" : app.deploy_state}`}
                  title={app.deploy_stage ?? undefined}
                >
                  <span className="hub-dot" />
                  {app.deploy_state === "deploying" && app.deploy_stage
                    ? app.deploy_stage
                    : DEPLOY_LABEL[app.deploy_state] ?? app.deploy_state}
                </span>

                <span className="hub-updated">
                  {new Date(app.updated_at).toLocaleDateString()}{" "}
                  {new Date(app.updated_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>

                <div className="hub-row-actions">
                  {live && app.url && (
                    <a
                      className="hub-open tip tip-top"
                      href={app.url}
                      target="_blank"
                      rel="noreferrer"
                      data-tooltip="Open app"
                      aria-label="Open app"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M7 17L17 7M9 7h8v8" />
                      </svg>
                    </a>
                  )}
                  {canEdit(app) && (
                    <button
                      className="hub-act tip tip-top"
                      type="button"
                      onClick={() => onBuild(app.id)}
                      data-tooltip={live ? "Edit in builder" : "Build"}
                      aria-label="Edit"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 20h9" />
                        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
                      </svg>
                    </button>
                  )}
                  {canEdit(app) && app.deploy_state !== "deploying" && (
                    <button
                      className="hub-act tip tip-top tip-end"
                      type="button"
                      onClick={() => setPendingDeploy(app)}
                      data-tooltip={live ? "Update deployment" : "Deploy"}
                      aria-label="Deploy"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 19V6M6 12l6-6 6 6" />
                        <path d="M5 21h14" />
                      </svg>
                    </button>
                  )}
                  {canEdit(app) && (
                    <button
                      className="hub-kebab tip tip-top tip-end"
                      type="button"
                      aria-label="More actions"
                      data-tooltip="More actions"
                      onClick={(e) => {
                        if (menu?.app.id === app.id) {
                          setMenu(null);
                          return;
                        }
                        const r = e.currentTarget.getBoundingClientRect();
                        setMenu({ app, top: r.bottom + 6, right: window.innerWidth - r.right });
                      }}
                    >
                      ⋯
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && apps.length < total && (
        <div className="hub-more">
          <button
            className="hub-more-btn"
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading…" : `Load more (${total - apps.length})`}
          </button>
        </div>
      )}

      {menu &&
        createPortal(
          <>
            <div className="hub-menu-backdrop" onClick={() => setMenu(null)} />
            <div className="hub-menu" style={{ top: menu.top, right: menu.right }}>
              {menu.app.deploy_state === "deploying" && (
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    cancelDeploy(menu.app.id);
                    setMenu(null);
                  }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M6 6l12 12M18 6L6 18" />
                  </svg>
                  Cancel deploy
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setPendingReview(menu.app);
                  setMenu(null);
                }}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 6h16M4 12h16M4 18h10" />
                </svg>
                Settings
              </button>
              {canOwn(menu.app) && (
                <button
                  type="button"
                  onClick={() => {
                    setPendingShare(menu.app);
                    setMenu(null);
                  }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <circle cx="18" cy="5" r="2.4" />
                    <circle cx="6" cy="12" r="2.4" />
                    <circle cx="18" cy="19" r="2.4" />
                    <path d="M8 11l8-5M8 13l8 5" />
                  </svg>
                  Share
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setPendingVersions(menu.app);
                  setMenu(null);
                }}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
                  <path d="M3 4v4h4" />
                  <path d="M12 8v4l3 2" />
                </svg>
                Versions
              </button>
              {canOwn(menu.app) && (
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    setPendingDelete(menu.app);
                    setMenu(null);
                  }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                  </svg>
                  Delete
                </button>
              )}
            </div>
          </>,
          document.body,
        )}

      {picker &&
        createPortal(
          <>
            <div className="hub-menu-backdrop" onClick={() => setPicker(null)} />
            <div className="icon-picker" style={{ top: picker.top, left: picker.left }}>
              <div className="ip-emojis">
                {EMOJIS.map((e) => (
                  <button
                    key={e}
                    type="button"
                    className={`ip-emoji ${picker.app.icon === e ? "sel" : ""}`}
                    onClick={() => patchApp(picker.app.id, { icon: e })}
                  >
                    {e}
                  </button>
                ))}
              </div>
              <div className="ip-colors">
                {COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`ip-color ${picker.app.color === c ? "sel" : ""}`}
                    style={{ background: c }}
                    aria-label={`Color ${c}`}
                    onClick={() => patchApp(picker.app.id, { color: c })}
                  />
                ))}
                <button
                  type="button"
                  className="ip-color ip-clear"
                  title="Remove color"
                  aria-label="Remove color"
                  onClick={() => patchApp(picker.app.id, { color: "" })}
                >
                  ×
                </button>
              </div>
            </div>
          </>,
          document.body,
        )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete app"
        message={
          <>
            You are about to delete <strong>{pendingDelete?.title}</strong> and its deployment
            (containers and images). This action cannot be undone.
          </>
        }
        confirmLabel="Delete"
        cancelLabel="Cancel"
        danger
        onConfirm={confirmRemove}
        onCancel={() => setPendingDelete(null)}
      />

      {pendingShare && (
        <ShareDialog
          appId={pendingShare.id}
          appTitle={pendingShare.title}
          onClose={() => setPendingShare(null)}
        />
      )}

      {pendingReview && (
        <SpecReviewDialog
          appId={pendingReview.id}
          appTitle={pendingReview.title}
          onClose={() => setPendingReview(null)}
          onSaved={refresh}
        />
      )}

      {pendingDeploy && (
        <DeployDialog
          app={pendingDeploy}
          onClose={() => setPendingDeploy(null)}
          onDeployed={refresh}
        />
      )}

      {pendingVersions && (
        <VersionsDialog
          appId={pendingVersions.id}
          appTitle={pendingVersions.title}
          deploying={pendingVersions.deploy_state === "deploying"}
          onClose={() => setPendingVersions(null)}
          onRollback={refresh}
        />
      )}
    </div>
  );
}
