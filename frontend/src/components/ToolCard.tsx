import { useState } from "react";
import type { ToolCall } from "../types";
import { Chevron } from "./Chevron";

export function ToolCard({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false); // contraída por defecto
  const pct = tool.progress != null ? Math.round(tool.progress * 100) : null;
  const done = tool.result != null;
  const iconState = done ? (tool.isError ? "error" : "done") : "running";

  return (
    <div className={`tool-card ${tool.isError ? "tool-error" : ""} ${open ? "open" : ""}`}>
      <button
        type="button"
        className="tool-head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={`tool-icon tool-icon-${iconState}`} aria-hidden="true" />
        <code className="tool-name">{tool.name}</code>
        {tool.longRunning && <span className="badge">long task</span>}
        <Chevron open={open} />
      </button>

      {/* Progreso visible aunque esté contraída (tareas largas). */}
      {tool.longRunning && !done && (
        <div className="progress">
          <div className="progress-bar" style={{ width: `${pct ?? 0}%` }} />
        </div>
      )}

      {open && (
        <div className="tool-body">
          <pre className="tool-input">{JSON.stringify(tool.input, null, 2)}</pre>
          {tool.progressMessage && !done && (
            <p className="tool-progress-msg">{tool.progressMessage}</p>
          )}
          {done && <pre className="tool-result">{tool.result}</pre>}
        </div>
      )}
    </div>
  );
}
