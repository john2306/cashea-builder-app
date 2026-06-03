import { useCallback, useEffect, useState } from "react";
import { getToken } from "../lib/auth";

interface McpConn {
  provider: string;
  label: string;
  connected: boolean;
}

export function McpButton() {
  const [items, setItems] = useState<McpConn[]>([]);

  const refresh = useCallback(() => {
    fetch("/api/mcp/connections")
      .then((r) => (r.ok ? r.json() : []))
      .then(setItems)
      .catch(() => undefined);
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

  if (items.length === 0) return null;

  const connect = (p: string) =>
    window.open(
      `/api/mcp/${p}/connect?token=${encodeURIComponent(getToken() ?? "")}`,
      "_blank",
      "width=560,height=720",
    );
  const disconnect = (p: string) =>
    fetch(`/api/mcp/${p}`, { method: "DELETE" }).then(refresh);

  return (
    <div className="mcp-bar">
      {items.map((it) =>
        it.connected ? (
          <span key={it.provider} className="mcp-chip connected">
            {it.label} MCP ✓
            <button
              className="mcp-x tip tip-top"
              type="button"
              aria-label="Desconectar"
              data-tooltip="Desconectar"
              onClick={() => disconnect(it.provider)}
            >
              ×
            </button>
          </span>
        ) : (
          <button
            key={it.provider}
            className="mcp-chip connect"
            type="button"
            onClick={() => connect(it.provider)}
          >
            Conectar {it.label} (MCP)
          </button>
        ),
      )}
    </div>
  );
}
