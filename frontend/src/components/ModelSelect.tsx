import { useEffect, useRef, useState } from "react";
import { MODELS } from "../lib/models";

export function ModelSelect({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const current = MODELS.find((m) => m.id === value) ?? MODELS[0];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div className="model-dd" ref={rootRef}>
      <button
        className="model-dd-trigger"
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Claude model"
      >
        <span className="model-dd-spark" aria-hidden="true">✦</span>
        <span className="model-dd-label">{current.label}</span>
        <span className={`chevron ${open ? "up" : ""}`} aria-hidden="true" />
      </button>

      {open && (
        <div className="model-dd-panel" role="listbox">
          <div className="model-dd-head">Model</div>
          {MODELS.map((m) => (
            <button
              key={m.id}
              type="button"
              role="option"
              aria-selected={m.id === value}
              className={`model-dd-option ${m.id === value ? "selected" : ""}`}
              onClick={() => {
                onChange(m.id);
                setOpen(false);
              }}
            >
              <span className="model-dd-option-main">
                <span className="model-dd-option-label">{m.label}</span>
                <span className="model-dd-option-hint">{m.hint}</span>
              </span>
              {m.id === value && <span className="model-dd-check" aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
