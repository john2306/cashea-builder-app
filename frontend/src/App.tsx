import { useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { useRouter } from "./router";
import { AgentsView } from "./views/AgentsView";
import { AppsView } from "./views/AppsView";
import { McpView } from "./views/McpView";
import { LogsView } from "./views/LogsView";
import { LoginScreen } from "./views/LoginScreen";
import { captureTokenFromUrl, currentUser } from "./lib/auth";

export default function App() {
  // Captura el #token del callback de Google (si llegó) ANTES de leer la sesión,
  // así no parpadea el login tras autenticar.
  const [user] = useState(() => {
    captureTokenFromUrl();
    return currentUser();
  });
  const { route, navigate } = useRouter();
  const [collapsed, setCollapsed] = useState(false);

  const buildApp = (appId: string) => navigate("agents", appId);

  if (!user) return <LoginScreen />;

  return (
    <div className={`platform ${collapsed ? "is-collapsed" : ""}`}>
      <Sidebar
        view={route.view}
        onSelect={(v) => navigate(v)}
        collapsed={collapsed}
        onToggle={() => setCollapsed((c) => !c)}
        user={user}
      />
      <main className="platform-main">
        {route.view === "agents" && <AgentsView openAppId={route.appId} />}
        {route.view === "apps" && <AppsView onBuild={buildApp} />}
        {route.view === "mcp" && <McpView />}
        {route.view === "logs" && <LogsView />}
      </main>
    </div>
  );
}
