import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

// Editor de la AppSpec previo al deploy. El usuario revisa/edita y confirma; recién ahí
// se pasa al modal de subdominio (onConfirm). Si la app no tiene spec, ofrece continuar.

interface Field {
  name: string;
  type: string;
}
interface Entity {
  name: string;
  source: string;
  location: string;
  fields: Field[];
}
interface Screen {
  name: string;
  type: string;
  entity: string;
  actions: string[];
}
interface Job {
  name: string;
  schedule: string;
  description: string;
}
interface AppSpec {
  name: string;
  description: string;
  data_sources: string[];
  entities: Entity[];
  screens: Screen[];
  jobs: Job[];
  notifications: string[];
}

const SOURCES = [
  "", "bigquery", "google_sheets", "google_docs", "google_drive", "gmail",
  "google_calendar", "notion", "slack", "cloud_storage", "llm", "none",
];
const FIELD_TYPES = ["string", "number", "date", "bool"];
const SCREEN_TYPES = ["table", "form", "dashboard", "detail"];
const ACTIONS = ["create", "update", "delete", "export", "notify"];

const EMPTY: AppSpec = {
  name: "", description: "", data_sources: [], entities: [], screens: [], jobs: [],
  notifications: [],
};

export function SpecReviewDialog({
  appId,
  appTitle,
  onClose,
  onSaved,
}: {
  appId: string;
  appTitle: string;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const [spec, setSpec] = useState<AppSpec | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    fetch(`/api/apps/${appId}/spec`)
      .then((r) => (r.ok ? r.json() : { spec: null }))
      .then((d) => setSpec(d.spec ? { ...EMPTY, ...d.spec } : null))
      .catch(() => setSpec(null))
      .finally(() => setLoading(false));
  }, [appId]);

  const patch = (p: Partial<AppSpec>) => setSpec((s) => (s ? { ...s, ...p } : s));

  // --- entities ---
  const setEntity = (i: number, e: Partial<Entity>) =>
    patch({ entities: spec!.entities.map((x, j) => (i === j ? { ...x, ...e } : x)) });
  const addEntity = () =>
    patch({ entities: [...spec!.entities, { name: "", source: "", location: "", fields: [] }] });
  const delEntity = (i: number) =>
    patch({ entities: spec!.entities.filter((_, j) => j !== i) });
  const setField = (ei: number, fi: number, f: Partial<Field>) =>
    setEntity(ei, { fields: spec!.entities[ei].fields.map((x, j) => (fi === j ? { ...x, ...f } : x)) });
  const addField = (ei: number) =>
    setEntity(ei, { fields: [...spec!.entities[ei].fields, { name: "", type: "string" }] });
  const delField = (ei: number, fi: number) =>
    setEntity(ei, { fields: spec!.entities[ei].fields.filter((_, j) => j !== fi) });

  // --- screens ---
  const setScreen = (i: number, s: Partial<Screen>) =>
    patch({ screens: spec!.screens.map((x, j) => (i === j ? { ...x, ...s } : x)) });
  const addScreen = () =>
    patch({ screens: [...spec!.screens, { name: "", type: "table", entity: "", actions: [] }] });
  const delScreen = (i: number) => patch({ screens: spec!.screens.filter((_, j) => j !== i) });
  const toggleAction = (i: number, a: string) => {
    const cur = spec!.screens[i].actions;
    setScreen(i, { actions: cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a] });
  };

  // --- jobs ---
  const setJob = (i: number, j: Partial<Job>) =>
    patch({ jobs: spec!.jobs.map((x, k) => (i === k ? { ...x, ...j } : x)) });
  const addJob = () =>
    patch({ jobs: [...spec!.jobs, { name: "", schedule: "", description: "" }] });
  const delJob = (i: number) => patch({ jobs: spec!.jobs.filter((_, k) => k !== i) });

  // --- chips (data_sources / notifications) ---
  const toggleList = (key: "data_sources" | "notifications", v: string) => {
    const cur = spec![key];
    patch({ [key]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] } as Partial<AppSpec>);
  };

  const save = async () => {
    if (!spec) {
      onClose();
      return;
    }
    setSaving(true);
    setError("");
    try {
      const r = await fetch(`/api/apps/${appId}/spec`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || "No se pudo guardar la configuración.");
      }
      onSaved?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al guardar.");
    } finally {
      setSaving(false);
    }
  };

  const entityNames = spec ? spec.entities.map((e) => e.name).filter(Boolean) : [];

  return createPortal(
    <div className="spec-drawer-overlay" onMouseDown={onClose}>
      <aside
        className="spec-drawer"
        role="dialog"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="spec-drawer-head">
          <div className="spec-drawer-titles">
            <p className="eyebrow">Configuración de la app</p>
            <h3>{appTitle}</h3>
          </div>
          <button className="spec-drawer-x" type="button" onClick={onClose} aria-label="Cerrar">✕</button>
        </header>

        <div className="spec-drawer-body">
        {loading ? (
          <p className="muted-note">Cargando…</p>
        ) : !spec ? (
          <p className="spec-empty">
            Esta app no tiene una configuración editable (p. ej. un dashboard). Editá su
            comportamiento desde el chat del Builder.
          </p>
        ) : (
          <div className="spec-body">
            {/* General */}
            <section className="spec-sec">
              <label className="spec-label">Nombre</label>
              <input
                className="spec-input"
                value={spec.name}
                onChange={(e) => patch({ name: e.target.value })}
              />
              <label className="spec-label">Descripción</label>
              <textarea
                className="spec-input"
                rows={2}
                value={spec.description}
                onChange={(e) => patch({ description: e.target.value })}
              />
            </section>

            {/* Entidades */}
            <section className="spec-sec">
              <div className="spec-sec-head">
                <h4>Entidades</h4>
                <button className="spec-add" type="button" onClick={addEntity}>+ Entidad</button>
              </div>
              {spec.entities.map((e, i) => (
                <div className="spec-card" key={i}>
                  <div className="spec-row">
                    <input
                      className="spec-input"
                      placeholder="Nombre"
                      value={e.name}
                      onChange={(ev) => setEntity(i, { name: ev.target.value })}
                    />
                    <select
                      className="spec-input spec-sel"
                      value={e.source}
                      onChange={(ev) => setEntity(i, { source: ev.target.value })}
                    >
                      {SOURCES.map((s) => (
                        <option key={s} value={s}>{s || "(fuente)"}</option>
                      ))}
                    </select>
                    <button className="spec-del" type="button" onClick={() => delEntity(i)}>✕</button>
                  </div>
                  <input
                    className="spec-input"
                    placeholder="location (id de Sheet, proyecto.dataset.tabla, …)"
                    value={e.location}
                    onChange={(ev) => setEntity(i, { location: ev.target.value })}
                  />
                  <div className="spec-fields">
                    {e.fields.map((f, fi) => (
                      <div className="spec-row spec-field-row" key={fi}>
                        <input
                          className="spec-input"
                          placeholder="campo"
                          value={f.name}
                          onChange={(ev) => setField(i, fi, { name: ev.target.value })}
                        />
                        <select
                          className="spec-input spec-sel"
                          value={f.type}
                          onChange={(ev) => setField(i, fi, { type: ev.target.value })}
                        >
                          {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <button className="spec-del" type="button" onClick={() => delField(i, fi)}>✕</button>
                      </div>
                    ))}
                    <button className="spec-add sm" type="button" onClick={() => addField(i)}>+ campo</button>
                  </div>
                </div>
              ))}
            </section>

            {/* Pantallas */}
            <section className="spec-sec">
              <div className="spec-sec-head">
                <h4>Pantallas</h4>
                <button className="spec-add" type="button" onClick={addScreen}>+ Pantalla</button>
              </div>
              {spec.screens.map((s, i) => (
                <div className="spec-card" key={i}>
                  <div className="spec-row">
                    <input
                      className="spec-input"
                      placeholder="Nombre"
                      value={s.name}
                      onChange={(ev) => setScreen(i, { name: ev.target.value })}
                    />
                    <select
                      className="spec-input spec-sel"
                      value={s.type}
                      onChange={(ev) => setScreen(i, { type: ev.target.value })}
                    >
                      {SCREEN_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <select
                      className="spec-input spec-sel"
                      value={s.entity}
                      onChange={(ev) => setScreen(i, { entity: ev.target.value })}
                    >
                      <option value="">(entidad)</option>
                      {entityNames.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                    <button className="spec-del" type="button" onClick={() => delScreen(i)}>✕</button>
                  </div>
                  <div className="spec-chips">
                    {ACTIONS.map((a) => (
                      <button
                        key={a}
                        type="button"
                        className={`spec-chip ${s.actions.includes(a) ? "on" : ""}`}
                        onClick={() => toggleAction(i, a)}
                      >
                        {a}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </section>

            {/* Tareas programadas */}
            <section className="spec-sec">
              <div className="spec-sec-head">
                <h4>Tareas programadas</h4>
                <button className="spec-add" type="button" onClick={addJob}>+ Tarea</button>
              </div>
              {spec.jobs.map((j, i) => (
                <div className="spec-card" key={i}>
                  <div className="spec-row">
                    <input
                      className="spec-input"
                      placeholder="Nombre"
                      value={j.name}
                      onChange={(ev) => setJob(i, { name: ev.target.value })}
                    />
                    <input
                      className="spec-input"
                      placeholder="cron (0 9 * * *)"
                      value={j.schedule}
                      onChange={(ev) => setJob(i, { schedule: ev.target.value })}
                    />
                    <button className="spec-del" type="button" onClick={() => delJob(i)}>✕</button>
                  </div>
                  <input
                    className="spec-input"
                    placeholder="Descripción"
                    value={j.description}
                    onChange={(ev) => setJob(i, { description: ev.target.value })}
                  />
                </div>
              ))}
            </section>

            {/* Fuentes / notificaciones */}
            <section className="spec-sec">
              <label className="spec-label">Fuentes de datos</label>
              <div className="spec-chips">
                {SOURCES.filter(Boolean).map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={`spec-chip ${spec.data_sources.includes(s) ? "on" : ""}`}
                    onClick={() => toggleList("data_sources", s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
              <label className="spec-label">Notificaciones</label>
              <div className="spec-chips">
                {["slack", "notion", "gmail"].map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={`spec-chip ${spec.notifications.includes(s) ? "on" : ""}`}
                    onClick={() => toggleList("notifications", s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </section>
          </div>
        )}
        </div>

        <footer className="spec-drawer-foot">
          {error && <p className="deploy-hint taken spec-drawer-err">{error}</p>}
          <button className="modal-btn ghost" type="button" onClick={onClose}>
            Cerrar
          </button>
          <button
            className="modal-btn primary"
            type="button"
            onClick={save}
            disabled={saving || loading || !spec}
          >
            {saving ? "Guardando…" : "Guardar cambios"}
          </button>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}
