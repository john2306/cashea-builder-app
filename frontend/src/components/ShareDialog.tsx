import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Dropdown } from "./Dropdown";

type Role = "view" | "edit";
interface Share {
  email: string;
  role: Role;
}

const ROLE_OPTIONS = [
  { value: "view", label: "Can view" },
  { value: "edit", label: "Can edit" },
];

/** Modal para gestionar quién accede a una app y con qué permiso (ver / editar).
 *  El dueño siempre conserva acceso y no se puede quitar. Eliminar la app = solo dueño/admin. */
export function ShareDialog({
  appId,
  appTitle,
  onClose,
}: {
  appId: string;
  appTitle: string;
  onClose: () => void;
}) {
  const [shares, setShares] = useState<Share[]>([]);
  const [owner, setOwner] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [role, setRole] = useState<Role>("view");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`/api/apps/${appId}/shares`)
      .then((r) => (r.ok ? r.json() : { shares: [], owner: null }))
      .then((d) => {
        setOwner(d.owner ?? null);
        setShares(d.shares ?? []);
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
    if (e && valid && e !== owner && !shares.some((s) => s.email === e)) {
      setShares((prev) => [...prev, { email: e, role }]);
    }
    setInput("");
    inputRef.current?.focus();
  };

  const setShareRole = (email: string, r: Role) =>
    setShares((prev) => prev.map((s) => (s.email === email ? { ...s, role: r } : s)));
  const remove = (email: string) =>
    setShares((prev) => prev.filter((s) => s.email !== email));

  const save = async () => {
    setSaving(true);
    await fetch(`/api/apps/${appId}/shares`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shares }),
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
          Choose who has access and with what permission. “Can edit” allows deploying and modifying, but
          <b> not deleting</b>: only the owner or an admin can delete the app.
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
            placeholder="email@company.com"
            type="email"
            autoFocus
          />
          <Dropdown
            className="share-add-role"
            value={role}
            options={ROLE_OPTIONS}
            onChange={(v) => setRole(v as Role)}
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
                  <span className="share-owner-badge" title="Owner · permanent access and full control">
                    Owner
                  </span>
                </div>
              )}
              {shares.length === 0 && !owner && (
                <p className="muted-note">No one else has access. Add emails.</p>
              )}
              {shares.map((s) => (
                <div className="share-item" key={s.email}>
                  <span className="share-item-email">{s.email}</span>
                  <Dropdown
                    className="share-item-role"
                    value={s.role}
                    options={ROLE_OPTIONS}
                    onChange={(v) => setShareRole(s.email, v as Role)}
                  />
                  <button
                    className="share-chip-x"
                    type="button"
                    aria-label={`Remove ${s.email}`}
                    title="Remove access"
                    onClick={() => remove(s.email)}
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
