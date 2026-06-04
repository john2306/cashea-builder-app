// Eventos que el backend envía por SSE (run desacoplado).
export type ServerEvent =
  | { type: "conversation"; conversation_id: string }
  | { type: "app"; app_id: string }
  | { type: "status"; state: "running" | "idle" }
  | { type: "token"; text: string }
  | { type: "thinking"; text: string }
  | {
      type: "tool_use";
      tool_use_id: string;
      name: string;
      input: Record<string, unknown>;
      long_running: boolean;
    }
  | {
      type: "tool_progress";
      tool_use_id: string;
      progress: number | null;
      message: string | null;
    }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error: boolean }
  | {
      type: "message_done";
      usage: {
        input_tokens: number;
        output_tokens: number;
        cache_read_input_tokens: number;
        cache_creation_input_tokens: number;
      };
      stop_reason: string;
    }
  | { type: "error"; message: string }
  | { type: "end" };

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  longRunning: boolean;
  progress: number | null;
  progressMessage: string | null;
  result: string | null;
  isError: boolean;
}

// Adjunto completo que se envía al backend (incluye los datos).
export type Attachment =
  | { kind: "text"; name: string; size: number; text: string }
  | { kind: "image"; name: string; size: number; media_type: string; data: string }
  | { kind: "document"; name: string; size: number; media_type: string; data: string }
  | { kind: "table"; name: string; size: number; format: string; data: string };

// Metadatos para mostrar el adjunto en la burbuja (sin los datos).
export interface AttachmentMeta {
  name: string;
  kind: Attachment["kind"];
  size: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  thinking: string;
  tools: ToolCall[];
  attachments?: AttachmentMeta[];
  usage?: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
  };
}

export interface FlowNodeData {
  label?: string;
  meta?: string;
  state?: "active" | "busy" | "idle" | "pending" | "error";
  [key: string]: unknown;
}

export interface FlowNodeRecord {
  id: string;
  type?: string;
  position: { x: number; y: number };
  data?: FlowNodeData;
  [key: string]: unknown;
}

export interface FlowEdgeRecord {
  id: string;
  source: string;
  target: string;
  label?: string;
  type?: string;
  animated?: boolean;
  [key: string]: unknown;
}

export interface AppFlow {
  nodes: FlowNodeRecord[];
  edges: FlowEdgeRecord[];
}

export interface AppIntegrations {
  mcp_servers: Record<string, unknown>[];
  apis: Record<string, unknown>[];
  tools: Record<string, unknown>[];
}

export type AppStatus = "draft" | "testing" | "production";
export type DeployState = "idle" | "deploying" | "deployed" | "error";

export interface Connector {
  id: string;
  label: string;
  configured: boolean;
}

export interface Connection {
  provider: string;
  account?: string | null;
  created_at: string;
}

export interface AppProject {
  id: string;
  conversation_id: string;
  title: string;
  icon?: string | null;
  color?: string | null;
  status: AppStatus;
  deploy_state: DeployState;
  deploy_stage?: string | null;
  slug?: string | null;
  url?: string | null;
  owner_email?: string | null;
  my_role?: "admin" | "owner" | "editor" | "viewer";
  created_at: string;
  updated_at: string;
  flow?: AppFlow;
  integrations?: AppIntegrations;
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: unknown;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  messages: MessageOut[];
}
