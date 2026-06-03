import { useEffect, useRef, useState } from "react";

export interface DropdownOption {
  value: string;
  label: string;
}

/** Select custom, minimalista y accesible (reemplaza al <select> nativo).
 *  Cierra al hacer clic fuera o con Escape; navegable con teclado básico. */
export function Dropdown({
  value,
  options,
  onChange,
  placeholder = "Seleccionar…",
  className = "",
}: {
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className={`dd ${open ? "open" : ""} ${className}`} ref={rootRef}>
      <button
        className={`dd-trigger ${current ? "" : "placeholder"}`}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="dd-value">{current?.label ?? placeholder}</span>
        <svg className="dd-caret" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="dd-panel" role="listbox">
          {options.map((o) => (
            <button
              key={o.value || "__all"}
              type="button"
              role="option"
              aria-selected={o.value === value}
              className={`dd-option ${o.value === value ? "selected" : ""}`}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
            >
              <span className="dd-option-label">{o.label}</span>
              {o.value === value && (
                <svg className="dd-check" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
