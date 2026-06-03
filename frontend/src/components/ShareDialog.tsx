import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** Modal para gestionar la allowlist de correos con acceso a una app (Enterprise). */
export function ShareDialog({
  appId,
  appTitle,
  onClose,
}: {
  appId: string;
  appTitle: string;
  onClose: () => void;
}) {
  const [emails, setEmails] = useState<string[]>([]);
  const [owner, setOwner] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`/api/apps/${appId}/shares`)
      .then((r) => (r.ok ? r.json() : { emails: [], owner: null }))
      .then((d) => {
        setOwner(d.owner ?? null);
        setEmails(d.emails ?? []);
      })
      .finally(() => setLoaded(true));
  }, [appId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const valid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(input.trim());

  const add = () => {
    const e = input.trim().toLowerCase();
    if (e && valid && !emails.includes(e)) setEmails((prev) => [...prev, e]);
    setInput("");
    inputRef.current?.focus();
  };

  const save = async () => {
    setSaving(true);
    await fetch(`/api/apps/${appId}/shares`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ emails }),
    });
    setSaving(false);
    onClose();
  };

  return createPortal(
    <div className="modal-overlay" onMouseDown={onClose}>
      <div
        className="modal-card share-modal"
        role="dialog"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 className="modal-title">Compartir “{appTitle}”</h3>
        <p className="modal-message">Solo estos correos acceden a la app. El resto verá “Sin acceso”.</p>

        <div className="share-add">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            placeholder="correo@empresa.com"
            type="email"
            autoFocus
          />
          <button
            className="modal-btn primary"
            type="button"
            onClick={add}
            disabled={!valid}
          >
            Agregar
          </button>
        </div>

        <div className="share-list">
          {!loaded ? (
            <p className="muted-note">Cargando…</p>
          ) : emails.length === 0 ? (
            <p className="muted-note">Nadie tiene acceso todavía. Agregá correos.</p>
          ) : (
            emails.map((e) =>
              e === owner ? (
                <span className="share-chip share-chip-owner" key={e}>
                  {e}
                  <span className="share-owner-badge" title="Dueño de la app · acceso permanente">
                    Propietario
                  </span>
                </span>
              ) : (
                <span className="share-chip" key={e}>
                  {e}
                  <button
                    className="share-chip-x"
                    type="button"
                    aria-label={`Quitar ${e}`}
                    title="Quitar acceso"
                    onClick={() => setEmails((prev) => prev.filter((x) => x !== e))}
                  >
                    ×
                  </button>
                </span>
              ),
            )
          )}
        </div>

        <div className="modal-actions">
          <button className="modal-btn ghost" type="button" onClick={onClose}>
            Cancelar
          </button>
          <button className="modal-btn primary" type="button" onClick={save} disabled={saving}>
            {saving ? "Guardando…" : "Guardar acceso"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
