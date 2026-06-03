import { viewPath } from "../router";
import { logout, type SessionUser } from "../lib/auth";

export type View = "agents" | "apps" | "mcp" | "logs";

function AgentsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="4" width="16" height="16" rx="4" />
      <circle cx="9" cy="10" r="1.4" />
      <circle cx="15" cy="10" r="1.4" />
      <path d="M9 15c1 1 5 1 6 0" />
    </svg>
  );
}
function AppsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function McpIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" />
      <path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" />
    </svg>
  );
}
function CollapseIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={collapsed ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
      <path d="M10 17l-5-5 5-5" />
      <path d="M15 12H5" />
    </svg>
  );
}

function LogsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 5h16M4 10h16M4 15h10M4 20h10" />
    </svg>
  );
}

const NAV: { id: View; label: string; icon: () => JSX.Element }[] = [
  { id: "agents", label: "Builder Agents", icon: AgentsIcon },
  { id: "apps", label: "Apps", icon: AppsIcon },
  { id: "mcp", label: "Connectors", icon: McpIcon },
];
// "Logs" solo para admin (se anexa dinámicamente según la sesión).
const LOGS_ITEM = { id: "logs" as View, label: "Logs", icon: LogsIcon };

function initialsOf(user?: SessionUser | null): string {
  const base = (user?.name || user?.email || "U").trim();
  const parts = base.split(/[\s@.]+/).filter(Boolean).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || "U";
}

export function Sidebar({
  view,
  onSelect,
  collapsed,
  onToggle,
  user,
}: {
  view: View;
  onSelect: (v: View) => void;
  collapsed: boolean;
  onToggle: () => void;
  user?: SessionUser | null;
}) {
  const displayName = user?.name || user?.email?.split("@")[0] || "Usuario";
  const email = user?.email || "";
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-brand">
        <span className="sidebar-logo">
          <img src="/logo32.png" alt="Cashea" />
        </span>
        {!collapsed && (
          <div className="sidebar-brand-text">
            <span className="sidebar-brand-name">Cashea Hub</span>
            <span className="sidebar-brand-sub">App</span>
          </div>
        )}
      </div>

      {!collapsed && <span className="sidebar-section">Workspace</span>}

      <nav className="sidebar-nav">
        {(user?.is_admin ? [...NAV, LOGS_ITEM] : NAV).map((item) => {
          const Icon = item.icon;
          return (
            <a
              key={item.id}
              href={viewPath(item.id)}
              className={`sidebar-item tip tip-right ${view === item.id ? "active" : ""}`}
              data-tooltip={item.label}
              onClick={(e) => {
                // Clic normal: navegación SPA. Cmd/Ctrl/medio: dejar abrir pestaña nueva.
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                e.preventDefault();
                onSelect(item.id);
              }}
            >
              <span className="sidebar-item-icon">
                <Icon />
              </span>
              {!collapsed && <span className="sidebar-item-label">{item.label}</span>}
            </a>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-profile tip tip-right" data-tooltip={email || displayName}>
          <span className="sidebar-avatar">
            {user?.picture ? (
              <img src={user.picture} alt="" referrerPolicy="no-referrer" />
            ) : (
              initialsOf(user)
            )}
          </span>
          {!collapsed && (
            <div className="sidebar-profile-info">
              <span className="sidebar-profile-name">{displayName}</span>
              <span className="sidebar-profile-email">{email}</span>
            </div>
          )}
          <button
            className="sidebar-logout tip tip-top"
            type="button"
            onClick={logout}
            aria-label="Cerrar sesión"
            data-tooltip="Cerrar sesión"
          >
            <LogoutIcon />
          </button>
        </div>
        <button
          className="sidebar-collapse tip tip-right"
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "Expandir" : "Contraer"}
          data-tooltip={collapsed ? "Expandir" : "Contraer"}
        >
          <CollapseIcon collapsed={collapsed} />
          {!collapsed && <span>Contraer</span>}
        </button>
      </div>
    </aside>
  );
}
