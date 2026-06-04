import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Version = { sha: string; date: string; message: string };

/** Historial de versiones desplegadas (git por app) + restaurar (rollback). */
export function VersionsDialog({
  appId,
  appTitle,
  deploying,
  onClose,
  onRollback,
}: {
  appId: string;
  appTitle: string;
  deploying: boolean;
  onClose: () => void;
  onRollback: () => void;
}) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/apps/${appId}/versions`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setVersions(Array.isArray(d) ? d : []))
      .catch(() => setVersions([]))
      .finally(() => setLoaded(true));
  }, [appId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rollback = async (sha: string) => {
    setBusy(sha);
    setError(null);
    try {
      const r = await fetch(`/api/apps/${appId}/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sha }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${r.status}`);
      }
      onRollback();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not restore.");
      setBusy(null);
    }
  };

  const fmt = (iso: string) =>
    new Date(iso).toLocaleString([], {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });

  return createPortal(
    <div className="modal-overlay" onMouseDown={onClose}>
      <div
        className="modal-card versions-modal"
        role="dialog"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 className="modal-title">Versions of “{appTitle}”</h3>
        <p className="modal-message">
          Deployment history. You can restore a previous version; it redeploys without
          regenerating the code.
        </p>

        {error && <div className="composer-error">{error}</div>}

        <div className="versions-list">
          {!loaded ? (
            <p className="muted-note">Loading…</p>
          ) : versions.length === 0 ? (
            <p className="muted-note">No versions yet. They are created on each deployment.</p>
          ) : (
            versions.map((v, i) => (
              <div className="version-row" key={v.sha}>
                <div className="version-info">
                  <span className="version-msg">{v.message}</span>
                  <span className="version-meta">
                    <code>{v.sha.slice(0, 7)}</code> · {fmt(v.date)}
                  </span>
                </div>
                {i === 0 ? (
                  <span className="version-tag">deployed</span>
                ) : (
                  <button
                    className="modal-btn ghost"
                    type="button"
                    disabled={deploying || busy !== null}
                    onClick={() => rollback(v.sha)}
                  >
                    {busy === v.sha ? "Restoring…" : "Restore"}
                  </button>
                )}
              </div>
            ))
          )}
        </div>

        <div className="modal-actions">
          <button className="modal-btn ghost" type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
