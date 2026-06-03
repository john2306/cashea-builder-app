export interface ConnectorMeta {
  id: string;
  label: string;
  icon: string;
  keywords: string[];
}

// Metadatos de UI (deben coincidir con backend/app/connectors.py).
export const CONNECTORS: ConnectorMeta[] = [
  { id: "gmail", label: "Gmail", icon: "📧", keywords: ["gmail", "correo electr", "email", "imap", "bandeja de entrada"] },
  { id: "google-drive", label: "Google Drive", icon: "📁", keywords: ["google drive", " drive"] },
  { id: "bigquery", label: "BigQuery", icon: "📊", keywords: ["bigquery", "big query"] },
  { id: "notion", label: "Notion", icon: "📝", keywords: ["notion"] },
  { id: "slack", label: "Slack", icon: "💬", keywords: ["slack"] },
  { id: "miro", label: "Miro", icon: "🗺️", keywords: ["miro"] },
];

export const CONNECTOR_BY_ID: Record<string, ConnectorMeta> = Object.fromEntries(
  CONNECTORS.map((c) => [c.id, c]),
);

// Detecta proveedores mencionados en un texto de conversación.
export function detectConnectors(text: string): string[] {
  const low = text.toLowerCase();
  return CONNECTORS.filter((c) => c.keywords.some((k) => low.includes(k))).map((c) => c.id);
}
