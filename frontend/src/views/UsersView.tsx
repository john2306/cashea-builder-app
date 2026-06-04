import { useCallback, useEffect, useState } from "react";
import { currentUser } from "../lib/auth";

interface UserRow {
  email: string;
  name: string | null;
  picture: string | null;
  role: "admin" | "member";
  is_env_admin: boolean;
  last_login_at: string | null;
  apps_count: number;
}

function initials(u: UserRow): string {
  const base = (u.name || u.email || "U").trim();
  return (
    base
      .split(/[\s@.]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase() ?? "")
      .join("") || "U"
  );
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleDateString("en", { day: "2-digit", month: "short", year: "numeric" });
}

const PAGE = 50; // paginación server-side (escala a 10k+ usuarios)

/** Gestión de usuarios (solo admin): lista usuarios y permite cambiar rol admin/member. */
export function UsersView() {
  const me = (currentUser()?.email || "").toLowerCase();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [admins, setAdmins] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [query, setQuery] = useState("");
  const [savingEmail, setSavingEmail] = useState("");
  const [error, setError] = useState("");

  const listUrl = useCallback(
    (offset: number, limit: number) => {
      const p = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (query.trim()) p.set("q", query.trim());
      return `/api/users?${p.toString()}`;
    },
    [query],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(listUrl(0, PAGE));
      if (r.status === 403) {
        setForbidden(true);
        return;
      }
      if (r.ok) {
        const d = await r.json();
        setUsers(d.items ?? []);
        setTotal(d.total ?? 0);
        setAdmins(d.admins ?? 0);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [listUrl]);

  // Re-trae los ya cargados (tras cambiar un rol) sin perder la cantidad.
  const refresh = useCallback(async () => {
    try {
      const r = await fetch(listUrl(0, Math.max(PAGE, users.length)));
      if (r.ok) {
        const d = await r.json();
        setUsers(d.items ?? []);
        setTotal(d.total ?? 0);
        setAdmins(d.admins ?? 0);
      }
    } catch {
      /* ignore */
    }
  }, [listUrl, users.length]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const r = await fetch(listUrl(users.length, PAGE));
      if (r.ok) {
        const d = await r.json();
        setUsers((prev) => [...prev, ...(d.items ?? [])]);
        setTotal(d.total ?? 0);
        setAdmins(d.admins ?? 0);
      }
    } catch {
      /* ignore */
    } finally {
      setLoadingMore(false);
    }
  }, [listUrl, users.length]);

  // (Re)carga la 1ra página al cambiar la búsqueda (debounce).
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const changeRole = async (email: string, role: string) => {
    if (email.toLowerCase() === me && role === "member") {
      const ok = window.confirm(
        "Remove your own admin role? You'll lose access to this section and to team management.",
      );
      if (!ok) return;
    }
    setSavingEmail(email);
    setError("");
    // Optimista: refleja el cambio al instante.
    setUsers((prev) =>
      prev.map((u) => (u.email === email ? { ...u, role: role as UserRow["role"] } : u)),
    );
    try {
      const r = await fetch(`/api/users/${encodeURIComponent(email)}/role`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setError(d.detail || "Could not change the role.");
        await refresh(); // revierte al estado real
      }
    } catch {
      setError("Network error while changing the role.");
      await refresh();
    } finally {
      setSavingEmail("");
    }
  };

  if (forbidden) {
    return (
      <div className="page">
        <header className="page-head">
          <h1>Users</h1>
        </header>
        <p className="muted-note">
          You don't have permission to manage users (admins only).
        </p>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-head">
        <h1>Users</h1>
        <p className="page-sub">Team roles and access.</p>
      </header>

      <div className="users-toolbar">
        <input
          className="users-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or email…"
          spellCheck={false}
        />
        <span className="users-count">
          {total} user{total === 1 ? "" : "s"} · {admins} admin
        </span>
      </div>

      {error && <p className="users-error">{error}</p>}

      {loading ? (
        <p className="muted-note">Loading…</p>
      ) : users.length === 0 ? (
        <p className="muted-note">{query.trim() ? "No matching users." : "No users yet."}</p>
      ) : (
        <div className="users-table">
          <div className="users-row users-row-head">
            <span>User</span>
            <span>Apps</span>
            <span>Last sign-in</span>
            <span>Role</span>
          </div>
          {users.map((u) => {
            const isSelf = u.email.toLowerCase() === me;
            // Solo es no-editable un admin permanente (ADMIN_EMAILS): ese correo siempre vuelve
            // a ser admin al iniciar sesión, así que es el salvavidas contra quedarse sin admins.
            // Lo demás (incluido bajarte a vos mismo) está permitido.
            const locked = u.is_env_admin;
            return (
              <div className="users-row" key={u.email}>
                <div className="users-user">
                  <span className="users-avatar">
                    {u.picture ? (
                      <img src={u.picture} alt="" referrerPolicy="no-referrer" />
                    ) : (
                      initials(u)
                    )}
                  </span>
                  <span className="users-id">
                    <span className="users-name">
                      {u.name || u.email.split("@")[0]}
                      {isSelf && <span className="users-you">You</span>}
                    </span>
                    <span className="users-email">{u.email}</span>
                  </span>
                </div>
                <span className="users-apps">{u.apps_count}</span>
                <span className="users-last">{fmtDate(u.last_login_at)}</span>
                <span className="users-role">
                  {locked ? (
                    <span className={`users-badge ${u.role}`}>
                      {u.role === "admin" ? "Admin" : "Member"}
                      {u.is_env_admin && (
                        <span className="users-fixed" title="Permanent admin (ADMIN_EMAILS)">
                          fixed
                        </span>
                      )}
                    </span>
                  ) : (
                    <div
                      className={`role-seg ${savingEmail === u.email ? "is-saving" : ""}`}
                      role="group"
                      aria-label="Role"
                    >
                      <button
                        type="button"
                        className={`member ${u.role === "member" ? "active" : ""}`}
                        onClick={() => u.role !== "member" && changeRole(u.email, "member")}
                      >
                        Member
                      </button>
                      <button
                        type="button"
                        className={`admin ${u.role === "admin" ? "active" : ""}`}
                        onClick={() => u.role !== "admin" && changeRole(u.email, "admin")}
                      >
                        Admin
                      </button>
                    </div>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {!loading && users.length < total && (
        <div className="hub-more">
          <button
            className="hub-more-btn"
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading…" : `Load more (${total - users.length})`}
          </button>
        </div>
      )}
    </div>
  );
}
