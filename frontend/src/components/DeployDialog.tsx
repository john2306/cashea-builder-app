import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AppProject } from "../types";

const SLUG_MAX = 21;
const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, SLUG_MAX);

type Avail = "idle" | "checking" | "available" | "taken" | "invalid";

/** Modal: elegir el subdominio (único) y desplegar la app. */
export function DeployDialog({
  app,
  onClose,
  onDeployed,
}: {
  app: AppProject;
  onClose: () => void;
  onDeployed: () => void;
}) {
  const [slug, setSlug] = useState(app.slug || slugify(app.title));
  const [avail, setAvail] = useState<Avail>("idle");
  const [normalized, setNormalized] = useState(slug);
  const [error, setError] = useState("");
  const [deploying, setDeploying] = useState(false);
  const [rebuild, setRebuild] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const s = slugify(slug);
    if (!s) {
      setAvail("invalid");
      setNormalized("");
      return;
    }
    setAvail("checking");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        const r = await fetch(`/api/apps/${app.id}/subdomain-check?slug=${encodeURIComponent(s)}`);
        const d = await r.json();
        setNormalized(d.slug);
        setAvail(d.available ? "available" : "taken");
      } catch {
        setAvail("idle");
      }
    }, 350);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [slug, app.id]);

  const deploy = async () => {
    setDeploying(true);
    setError("");
    const r = await fetch(`/api/apps/${app.id}/deploy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: normalized, rebuild }),
    });
    setDeploying(false);
    if (r.ok) {
      onDeployed();
      onClose();
    } else {
      const d = await r.json().catch(() => ({}));
      setError(d.detail || "No se pudo desplegar.");
    }
  };

  const hint: Record<Avail, string> = {
    idle: "",
    checking: "Verificando disponibilidad…",
    available: "✓ Disponible",
    taken: "✗ Ya está en uso — elegí otro",
    invalid: "Usá letras, números y guiones",
  };
  const canDeploy = avail === "available" && !deploying;

  return createPortal(
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="modal-card deploy-modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <h3 className="modal-title">Desplegar “{app.title}”</h3>
        <p className="modal-message">Elegí el subdominio público de la app.</p>

        <div className="deploy-field">
          <div className="deploy-field-head">
            <label className="deploy-label">Subdominio</label>
            <span className="deploy-count">{slugify(slug).length}/{SLUG_MAX}</span>
          </div>
          <div className="deploy-slug">
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value.slice(0, SLUG_MAX))}
              placeholder="mi-app"
              maxLength={SLUG_MAX}
              autoFocus
              spellCheck={false}
            />
            <span className="deploy-domain">.localhost:5173</span>
          </div>
          <p className={`deploy-hint ${error ? "taken" : avail}`}>
            {error || hint[avail]}
          </p>
          {normalized && !error && (
            <p className="deploy-url">http://{normalized}.localhost:5173</p>
          )}
        </div>

        <label className={`deploy-rebuild ${rebuild ? "on" : ""}`}>
          <input type="checkbox" checked={rebuild} onChange={(e) => setRebuild(e.target.checked)} />
          <span className="deploy-rebuild-text">
            <b>Reconstrucción total</b>
            <small>Regenera el código desde cero · más lenta</small>
          </span>
        </label>

        <div className="modal-actions">
          <button className="modal-btn ghost" type="button" onClick={onClose}>
            Cancelar
          </button>
          <button className="modal-btn primary" type="button" onClick={deploy} disabled={!canDeploy}>
            {deploying ? "Desplegando…" : "Desplegar"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
