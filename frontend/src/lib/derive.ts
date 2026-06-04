import type { AppFlow, AppIntegrations, ChatMessage } from "../types";

const URL_RE = /https?:\/\/[^\s)"']+/g;

// Servicios externos conocidos que requieren autenticación (OAuth / MCP).
// Si la conversación los menciona, aparecen automáticamente en Integrations.
interface ServiceDef {
  id: string;
  label: string;
  keywords: string[];
}

const EXTERNAL_SERVICES: ServiceDef[] = [
  { id: "gmail", label: "Gmail", keywords: ["gmail", "imap", "bandeja de entrada", "correo electr", "email trigger"] },
  { id: "google-sheets", label: "Google Sheets", keywords: ["google sheet", "spreadsheet", "hoja de cálculo", "sheets"] },
  { id: "google-drive", label: "Google Drive", keywords: ["google drive", " drive"] },
  { id: "google-calendar", label: "Google Calendar", keywords: ["google calendar", "calendario"] },
  { id: "outlook", label: "Outlook", keywords: ["outlook", "office 365", "microsoft 365"] },
  { id: "slack", label: "Slack", keywords: ["slack"] },
  { id: "notion", label: "Notion", keywords: ["notion"] },
  { id: "hubspot", label: "HubSpot", keywords: ["hubspot"] },
  { id: "salesforce", label: "Salesforce", keywords: ["salesforce"] },
  { id: "stripe", label: "Stripe", keywords: ["stripe"] },
  { id: "github", label: "GitHub", keywords: ["github"] },
  { id: "jira", label: "Jira", keywords: ["jira"] },
  { id: "dropbox", label: "Dropbox", keywords: ["dropbox"] },
];

// ---------- Integraciones ----------

export function deriveIntegrations(messages: ChatMessage[]): AppIntegrations {
  const apis = new Map<string, Record<string, unknown>>();
  const tools = new Map<string, Record<string, unknown>>();
  const external = new Map<string, Record<string, unknown>>();

  const allText = messages.map((m) => m.text).join("\n").toLowerCase();
  for (const svc of EXTERNAL_SERVICES) {
    if (svc.keywords.some((k) => allText.includes(k))) {
      external.set(svc.id, {
        id: svc.id,
        name: svc.label,
        label: svc.label,
        meta: "Requires authentication",
        state: "pending",
      });
    }
  }

  for (const message of messages) {
    for (const match of message.text.matchAll(URL_RE)) {
      const url = match[0];
      apis.set(url, { id: url, url, label: url, meta: "Detected in the conversation", state: "active" });
    }

    for (const tool of message.tools) {
      const item = {
        id: tool.name,
        name: tool.name,
        label: tool.name,
        meta: tool.longRunning ? "Long task" : "Tool call",
        state: tool.isError ? "error" : tool.result ? "active" : "busy",
      };
      tools.set(tool.name, item);
      if (tool.name.toLowerCase().includes("mcp")) {
        external.set(tool.name, { ...item, meta: "MCP / requires auth", state: "pending" });
      }
      for (const match of JSON.stringify(tool.input).matchAll(URL_RE)) {
        const url = match[0];
        apis.set(url, { id: url, url, label: url, meta: `Used by ${tool.name}`, state: "active" });
      }
    }
  }

  return {
    mcp_servers: Array.from(external.values()),
    apis: Array.from(apis.values()),
    tools: Array.from(tools.values()),
  };
}

// ---------- Flujo (nodos + conexiones) ----------

function cleanLabel(raw: string): string {
  return raw
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/^\d+[.)]\s*/, "")
    .split(/\s*[:(]/)[0]
    .trim()
    .slice(0, 48);
}

function extractTableSteps(text: string): string[] {
  const rows = text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.startsWith("|"));
  if (rows.length < 2) return [];

  const steps: string[] = [];
  let headerSkipped = false;
  for (const row of rows) {
    const cells = row
      .split("|")
      .map((c) => c.trim())
      .filter((c, i, arr) => !((i === 0 || i === arr.length - 1) && c === ""));
    if (cells.length === 0) continue;
    // Fila separadora |---|---|
    if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
    // Saltar la cabecera (primera fila de datos si parece encabezado)
    if (!headerSkipped) {
      headerSkipped = true;
      if (/^(etapa|paso|fase|stage|step|#|nº|orden)$/i.test(cells[0])) continue;
    }
    const label = cleanLabel(cells[0]);
    if (label) steps.push(label);
  }
  return steps;
}

function extractNumberedSteps(text: string): string[] {
  const steps: string[] = [];
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (m) {
      const label = cleanLabel(m[1]);
      if (label) steps.push(label);
    }
  }
  return steps;
}

function extractSteps(text: string): string[] {
  const table = extractTableSteps(text);
  if (table.length >= 2) return table;
  // Listas numeradas: solo si el mensaje habla de un flujo/pipeline (evita listas de opciones).
  if (/flujo|pipeline|paso|trigger|etapa|proceso|workflow|automatiza/i.test(text)) {
    const numbered = extractNumberedSteps(text);
    if (numbered.length >= 2) return numbered;
  }
  return [];
}

function buildFlow(steps: string[]): AppFlow {
  const limited = steps.slice(0, 16);
  const nodes = limited.map((label, i) => ({
    id: `auto-${i}`,
    type: "default",
    position: { x: 160, y: 40 + i * 104 },
    data: { label: label || `Step ${i + 1}` },
  }));
  const edges = limited.slice(1).map((_, i) => ({
    id: `auto-e-${i}`,
    source: `auto-${i}`,
    target: `auto-${i + 1}`,
    type: "smoothstep",
    animated: true,
  }));
  return { nodes, edges };
}

// Construye el flujo a partir del mensaje del asistente más reciente que describa pasos.
export function deriveFlow(messages: ChatMessage[]): AppFlow {
  const assistant = messages.filter((m) => m.role === "assistant" && m.text.trim());
  for (let i = assistant.length - 1; i >= 0; i--) {
    const steps = extractSteps(assistant[i].text);
    if (steps.length >= 2) return buildFlow(steps);
  }
  return { nodes: [], edges: [] };
}
