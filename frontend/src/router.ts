import { useCallback, useEffect, useState } from "react";
import type { View } from "./components/Sidebar";

// Mapa vista <-> ruta. URLs reales, compartibles y persistentes al recargar.
const VIEW_TO_PATH: Record<View, string> = {
  agents: "/agents",
  apps: "/apps",
  mcp: "/connectors",
  logs: "/logs",
};

const PATH_TO_VIEW: Record<string, View> = {
  "/agents": "agents",
  "/apps": "apps",
  "/connectors": "mcp",
  "/logs": "logs",
};

const TITLES: Record<View, string> = {
  agents: "Builder Agents",
  apps: "Apps",
  mcp: "Connectors",
  logs: "Logs",
};

export interface Route {
  view: View;
  appId: string | null;
}

function parse(pathname: string): Route {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  const base = "/" + (parts[0] ?? "");
  const view = PATH_TO_VIEW[base] ?? "agents";
  // Deep-link a una app concreta: /agents/<appId>
  const appId = view === "agents" && parts[1] ? parts[1] : null;
  return { view, appId };
}

function toPath(view: View, appId?: string | null): string {
  const base = VIEW_TO_PATH[view];
  return view === "agents" && appId ? `${base}/${appId}` : base;
}

/** Ruta base de una vista (para `href` de los enlaces del sidebar). */
export function viewPath(view: View): string {
  return VIEW_TO_PATH[view];
}

export function useRouter() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.pathname));

  useEffect(() => {
    const onPop = () => setRoute(parse(window.location.pathname));
    window.addEventListener("popstate", onPop);
    // Normaliza "/" -> "/agents" sin agregar al historial.
    if (window.location.pathname === "/") {
      window.history.replaceState(null, "", "/agents");
    }
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    document.title = `${TITLES[route.view]} · Cashea Hub App`;
  }, [route.view]);

  const navigate = useCallback((view: View, appId?: string | null) => {
    const path = toPath(view, appId);
    if (path !== window.location.pathname) {
      window.history.pushState(null, "", path);
    }
    setRoute({ view, appId: appId ?? null });
  }, []);

  return { route, navigate };
}
