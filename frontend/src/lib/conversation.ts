import type { ChatMessage, MessageOut, ToolCall } from "../types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function blockText(block: Record<string, unknown>) {
  if (block.type === "text" && typeof block.text === "string") return block.text;
  if (block.type === "thinking" && typeof block.thinking === "string") return block.thinking;
  return "";
}

function textFromContent(content: unknown) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter(isRecord).map(blockText).filter(Boolean).join("\n\n");
}

function toolFromBlock(block: Record<string, unknown>): ToolCall | null {
  if (block.type !== "tool_use") return null;
  const id = typeof block.id === "string" ? block.id : `tool-${Date.now()}`;
  const name = typeof block.name === "string" ? block.name : "tool";
  const input = isRecord(block.input) ? block.input : {};
  return {
    id,
    name,
    input,
    longRunning: false,
    progress: null,
    progressMessage: null,
    result: null,
    isError: false,
  };
}

function isToolCall(value: ToolCall | null): value is ToolCall {
  return value !== null;
}

function applyToolResults(messages: ChatMessage[], content: unknown) {
  if (!Array.isArray(content)) return false;
  const results = content.filter(isRecord).filter((block) => block.type === "tool_result");
  if (results.length === 0) return false;

  const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  if (!latestAssistant) return true;

  latestAssistant.tools = latestAssistant.tools.map((tool) => {
    const result = results.find((block) => block.tool_use_id === tool.id);
    if (!result) return tool;
    return {
      ...tool,
      result: typeof result.content === "string" ? result.content : JSON.stringify(result.content),
      isError: result.is_error === true,
      progress: result.is_error === true ? tool.progress : 1,
    };
  });

  return true;
}

export function hydrateMessages(rows: MessageOut[]): ChatMessage[] {
  const messages: ChatMessage[] = [];

  for (const row of rows) {
    // Marcador de sistema (p. ej. deploy): se muestra como un hito (pill) en la conversación.
    if (row.role === "system" && isRecord(row.content) && row.content.type === "deploy") {
      const c = row.content;
      messages.push({
        id: row.id,
        role: "system",
        text: "",
        thinking: "",
        tools: [],
        marker: {
          kind: "deploy",
          url: typeof c.url === "string" ? c.url : null,
          sha: typeof c.sha === "string" ? c.sha : null,
          label: typeof c.label === "string" ? c.label : "Deployed",
        },
      });
      continue;
    }

    if (row.role === "user" && applyToolResults(messages, row.content)) continue;

    const blocks = Array.isArray(row.content) ? row.content.filter(isRecord) : [];
    const tools = row.role === "assistant" ? blocks.map(toolFromBlock).filter(isToolCall) : [];
    const thinking = blocks
      .filter((block) => block.type === "thinking")
      .map(blockText)
      .filter(Boolean)
      .join("\n\n");
    const text = textFromContent(row.content);

    if (!text && tools.length === 0 && !thinking) continue;

    messages.push({
      id: row.id,
      role: row.role,
      text,
      thinking,
      tools,
    });
  }

  return messages;
}
