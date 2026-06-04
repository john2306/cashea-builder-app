import { useEffect, useRef, useState } from "react";
import { DayPicker, type DateRange } from "react-day-picker";
import { enUS } from "react-day-picker/locale";
import "react-day-picker/style.css";

/** Convierte "YYYY-MM-DD" en Date local (sin corrimiento de zona horaria). */
function parse(value: string): Date | undefined {
  if (!value) return undefined;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d);
}

/** Serializa un Date a "YYYY-MM-DD" (formato que espera el backend). */
function serialize(date?: Date): string {
  if (!date) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}`;
}

function fmt(date?: Date): string {
  return date
    ? date.toLocaleDateString("en", { day: "2-digit", month: "2-digit", year: "numeric" })
    : "";
}

/** Selector de rango de fechas con un solo calendario minimalista (react-day-picker). */
export function DateRangeField({
  from,
  to,
  onChange,
  placeholder = "Date range",
}: {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  // Cuenta clics desde que se abre el panel: solo cerramos en el 2º (fecha final).
  const picks = useRef(0);
  const range: DateRange | undefined = parse(from)
    ? { from: parse(from), to: parse(to) }
    : undefined;

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

  const fromLabel = fmt(range?.from);
  const toLabel = fmt(range?.to);
  const hasValue = Boolean(fromLabel);

  return (
    <div className={`datefield daterange ${open ? "open" : ""}`} ref={rootRef}>
      <button
        className={`dd-trigger ${hasValue ? "" : "placeholder"}`}
        type="button"
        onClick={() =>
          setOpen((v) => {
            if (!v) picks.current = 0; // al abrir, reiniciamos el conteo
            return !v;
          })
        }
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <svg className="datefield-icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3" y="4.5" width="18" height="16" rx="2.5" />
          <path d="M3 9h18M8 2.5v4M16 2.5v4" />
        </svg>
        <span className="dd-value">
          {hasValue ? `${fromLabel} – ${toLabel || "…"}` : placeholder}
        </span>
      </button>

      {open && (
        <div className="datefield-panel" role="dialog">
          <DayPicker
            mode="range"
            locale={enUS}
            selected={range}
            defaultMonth={range?.from}
            showOutsideDays
            onSelect={(r) => {
              onChange(serialize(r?.from), serialize(r?.to));
              picks.current += 1;
              // 1er clic = fecha inicial (mantener abierto); 2º clic = fecha final (cerrar).
              if (picks.current >= 2 && r?.from && r?.to) {
                picks.current = 0;
                setOpen(false);
              }
            }}
          />
          {hasValue && (
            <button
              className="datefield-clear"
              type="button"
              onClick={() => {
                onChange("", "");
                setOpen(false);
              }}
            >
              Clear range
            </button>
          )}
        </div>
      )}
    </div>
  );
}
