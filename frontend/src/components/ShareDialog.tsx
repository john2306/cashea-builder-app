import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** Modal para compartir una app (SOLO lectura). Quien recibe acceso puede VER, no editar.
 *  El dueño siempre conserva acceso. Editar/eliminar/compartir = solo el dueño. */
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
    if (e && valid && e !== owner && !emails.includes(e)) {
      setEmails((prev) => [...prev, e]);
    }
    setInput("");
    inputRef.current?.focus();
  };
  const remove = (email: string) => setEmails((prev) => prev.filter((x) => x !== email));

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
        <h3 className="modal-title">Share “{appTitle}”</h3>
        <p className="modal-message">
          Anyone you add can <b>view</b> this app (read-only). Only you, the owner, can edit,
          delete or manage sharing.
        </p>

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
            placeholder="name@company.com"
            type="email"
            autoFocus
          />
          <button className="modal-btn primary" type="button" onClick={add} disabled={!valid}>
            Add
          </button>
        </div>

        <div className="share-list">
          {!loaded ? (
            <p className="muted-note">Loading…</p>
          ) : (
            <>
              {owner && (
                <div className="share-item is-owner">
                  <span className="share-item-email">{owner}</span>
                  <span className="share-owner-badge" title="Owner · full access">
                    Owner
                  </span>
                </div>
              )}
              {emails.length === 0 && !owner && (
                <p className="muted-note">No one else has access yet. Add emails.</p>
              )}
              {emails.map((e) => (
                <div className="share-item" key={e}>
                  <span className="share-item-email">{e}</span>
                  <span className="share-view-tag">View</span>
                  <button
                    className="share-chip-x"
                    type="button"
                    aria-label={`Remove ${e}`}
                    title="Remove access"
                    onClick={() => remove(e)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="modal-actions">
          <button className="modal-btn ghost" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="modal-btn primary" type="button" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save access"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
