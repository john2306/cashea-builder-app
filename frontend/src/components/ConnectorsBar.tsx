import { CONNECTOR_BY_ID } from "../lib/connectors";
import type { Connection, Connector } from "../types";

export function ConnectorsBar({
  detected,
  connectors,
  connections,
  onConnect,
  onDisconnect,
}: {
  detected: string[];
  connectors: Connector[];
  connections: Connection[];
  onConnect: (provider: string) => void;
  onDisconnect: (provider: string) => void;
}) {
  const configured = new Map(connectors.map((c) => [c.id, c.configured]));
  const connected = new Map(connections.map((c) => [c.provider, c]));

  // Unión de los detectados en la conversación + los ya conectados.
  const ids = Array.from(new Set([...detected, ...connections.map((c) => c.provider)]));
  if (ids.length === 0) return null;

  return (
    <div className="connectors-bar">
      <span className="connectors-label">Connectors</span>
      <div className="connectors-list">
        {ids.map((id) => {
          const meta = CONNECTOR_BY_ID[id];
          if (!meta) return null;
          const conn = connected.get(id);
          const isConfigured = configured.get(id) ?? false;

          if (conn) {
            return (
              <div className="connector-chip connected" key={id} title={conn.account ?? ""}>
                <span className="connector-icon">{meta.icon}</span>
                <span className="connector-name">{meta.label}</span>
                <span className="connector-check" aria-hidden="true">✓</span>
                <button
                  className="connector-x tip tip-top"
                  type="button"
                  aria-label={`Disconnect ${meta.label}`}
                  data-tooltip="Disconnect"
                  onClick={() => onDisconnect(id)}
                >
                  ×
                </button>
              </div>
            );
          }

          return (
            <button
              className="connector-chip connect"
              key={id}
              type="button"
              disabled={!isConfigured}
              title={isConfigured ? "" : `${meta.label} credentials still need to be configured`}
              onClick={() => onConnect(id)}
            >
              <span className="connector-icon">{meta.icon}</span>
              <span className="connector-name">Connect {meta.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
