import { useState } from "react";
import type { ChatMessage } from "../types";
import { AttachmentChip } from "./AttachmentChip";
import { Chevron } from "./Chevron";
import { Markdown } from "./Markdown";
import { ToolCard } from "./ToolCard";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const [showThinking, setShowThinking] = useState(false);
  const isUser = message.role === "user";

  // No renderizar el bubble vacío del asistente (placeholder mientras streamea);
  // el indicador "El agente está trabajando…" lo cubre.
  const isEmpty =
    !isUser &&
    !message.text &&
    message.tools.length === 0 &&
    !message.thinking &&
    !(message.attachments && message.attachments.length);
  if (isEmpty) return null;

  return (
    <div className={`bubble-row ${isUser ? "from-user" : "from-assistant"}`}>
      {!isUser && <div className="bubble-avatar assistant-avatar" aria-hidden="true" />}

      <div className={`bubble ${isUser ? "bubble-user" : "bubble-assistant"}`}>
        {message.attachments && message.attachments.length > 0 && (
          <div className="chips chips-in-bubble">
            {message.attachments.map((a, i) => (
              <AttachmentChip key={i} att={a} />
            ))}
          </div>
        )}

        {!isUser && message.thinking && (
          <div className="thinking">
            <button
              className="thinking-toggle"
              onClick={() => setShowThinking((v) => !v)}
              aria-expanded={showThinking}
            >
              <Chevron open={showThinking} />
              Reasoning
            </button>
            {showThinking && <pre className="thinking-body">{message.thinking}</pre>}
          </div>
        )}

        {message.tools.map((t) => (
          <ToolCard key={t.id} tool={t} />
        ))}

        {message.text &&
          (isUser ? (
            <div className="bubble-text">{message.text}</div>
          ) : (
            <Markdown>{message.text}</Markdown>
          ))}

        {message.usage && (
          <div className="usage">
            {message.usage.output_tokens} tokens generated {" / "}
            {message.usage.cache_read_input_tokens > 0
              ? `${message.usage.cache_read_input_tokens} from cache`
              : "no cache"}
          </div>
        )}
      </div>

      {isUser && <div className="bubble-avatar user-avatar" aria-hidden="true" />}
    </div>
  );
}
