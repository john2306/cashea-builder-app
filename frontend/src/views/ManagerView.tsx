import { useCallback, useEffect, useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";

// ----------------------------------------------------------------------------- //
// Tipos
// ----------------------------------------------------------------------------- //
interface Skill {
  name: string;
  description: string;
  when_to_use: string;
  body: string;
  enabled: boolean;
  built_in: boolean;
  updated_at: string | null;
}

interface ConnectorTool {
  name: string;
  description: string;
}

interface Connector {
  provider: string;
  label: string;
  transport: string;
  agent_hint: string;
  enabled: boolean;
  tools: ConnectorTool[];
}

type Tab = "skills" | "connectors";

const EMPTY_DRAFT: Skill = {
  name: "",
  description: "",
  when_to_use: "",
  body: "",
  enabled: true,
  built_in: false,
  updated_at: null,
};

// ----------------------------------------------------------------------------- //
// Skills
// ----------------------------------------------------------------------------- //
function SkillsTab() {
  const [items, setItems] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Skill | null>(null); // draft en edición/creación
  const [isNew, setIsNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDel, setConfirmDel] = useState<Skill | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/admin/skills");
      if (r.ok) setItems((await r.json()).items ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (s: Skill) => {
    setItems((prev) => prev.map((x) => (x.name === s.name ? { ...x, enabled: !x.enabled } : x)));
    await fetch(`/api/admin/skills/${encodeURIComponent(s.name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !s.enabled }),
    }).catch(() => void load());
  };

  const doRemove = async (s: Skill) => {
    setItems((prev) => prev.filter((x) => x.name !== s.name));
    await fetch(`/api/admin/skills/${encodeURIComponent(s.name)}`, { method: "DELETE" }).catch(
      () => void load(),
    );
  };

  const save = async () => {
    if (!editing) return;
    setError("");
    setSaving(true);
    try {
      const url = isNew ? "/api/admin/skills" : `/api/admin/skills/${encodeURIComponent(editing.name)}`;
      const r = await fetch(url, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: editing.name,
          description: editing.description,
          when_to_use: editing.when_to_use,
          body: editing.body,
          enabled: editing.enabled,
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setError(d.detail || "Could not save the skill.");
        return;
      }
      setEditing(null);
      await load();
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <div className="mgr-editor">
        <div className="mgr-editor-head">
          <h2>{isNew ? "New skill" : `Edit · ${editing.name}`}</h2>
          <div className="mgr-editor-actions">
            <button className="btn-ghost" onClick={() => setEditing(null)} disabled={saving}>
              Cancel
            </button>
            <button className="btn-primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
        {error && <p className="users-error">{error}</p>}
        <label className="mgr-field">
          <span>Name (slug)</span>
          <input
            value={editing.name}
            disabled={!isNew}
            placeholder="e.g. approval-workflow"
            onChange={(e) =>
              setEditing({ ...editing, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })
            }
          />
        </label>
        <label className="mgr-field">
          <span>Description</span>
          <input
            value={editing.description}
            placeholder="One line: what kind of app this playbook builds."
            onChange={(e) => setEditing({ ...editing, description: e.target.value })}
          />
        </label>
        <label className="mgr-field">
          <span>When to use</span>
          <input
            value={editing.when_to_use}
            placeholder="When should the agent pick this skill?"
            onChange={(e) => setEditing({ ...editing, when_to_use: e.target.value })}
          />
        </label>
        <label className="mgr-field">
          <span>Playbook (Markdown)</span>
          <textarea
            value={editing.body}
            rows={18}
            spellCheck={false}
            placeholder="# Playbook…  Step-by-step guidance for the agent."
            onChange={(e) => setEditing({ ...editing, body: e.target.value })}
          />
        </label>
      </div>
    );
  }

  return (
    <>
      <div className="mgr-toolbar">
        <span className="users-count">
          {items.length} skill{items.length === 1 ? "" : "s"} ·{" "}
          {items.filter((s) => s.enabled).length} enabled
        </span>
        <button
          className="btn-primary"
          onClick={() => {
            setIsNew(true);
            setEditing({ ...EMPTY_DRAFT });
            setError("");
          }}
        >
          + New skill
        </button>
      </div>
      {loading ? (
        <p className="muted-note">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted-note">No skills yet.</p>
      ) : (
        <div className="mgr-list">
          {items.map((s) => (
            <div className={`mgr-card ${s.enabled ? "" : "is-off"}`} key={s.name}>
              <div className="mgr-card-main">
                <div className="mgr-card-title">
                  <code className="mgr-slug">{s.name}</code>
                  {s.built_in && <span className="mgr-badge">built-in</span>}
                  {!s.enabled && <span className="mgr-badge off">disabled</span>}
                </div>
                <p className="mgr-card-desc">{s.description || "—"}</p>
                {s.when_to_use && <p className="mgr-card-when">Use when: {s.when_to_use}</p>}
              </div>
              <div className="mgr-card-actions">
                <button
                  className={`mgr-switch ${s.enabled ? "on" : ""}`}
                  onClick={() => toggle(s)}
                  title={s.enabled ? "Disable" : "Enable"}
                  aria-label="Toggle enabled"
                >
                  <span />
                </button>
                <button
                  className="btn-ghost"
                  onClick={() => {
                    setIsNew(false);
                    setEditing({ ...s });
                    setError("");
                  }}
                >
                  Edit
                </button>
                <button className="btn-ghost danger" onClick={() => setConfirmDel(s)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!confirmDel}
        title="Delete skill"
        message={
          <>
            Delete the skill <b>{confirmDel?.name}</b>? This can't be undone.
          </>
        }
        confirmLabel="Delete"
        danger
        onCancel={() => setConfirmDel(null)}
        onConfirm={() => {
          if (confirmDel) void doRemove(confirmDel);
          setConfirmDel(null);
        }}
      />
    </>
  );
}

// ----------------------------------------------------------------------------- //
// Connectors / Tools
// ----------------------------------------------------------------------------- //
function ConnectorsTab() {
  const [items, setItems] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/admin/connectors");
      if (r.ok) setItems((await r.json()).items ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (c: Connector) => {
    setItems((prev) =>
      prev.map((x) => (x.provider === c.provider ? { ...x, enabled: !x.enabled } : x)),
    );
    await fetch(`/api/admin/connectors/${encodeURIComponent(c.provider)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !c.enabled }),
    }).catch(() => void load());
  };

  if (loading) return <p className="muted-note">Loading…</p>;

  return (
    <>
      <p className="page-sub mgr-hint">
        Disabling a connector hides its tools from the Builder agent and blocks deployed apps from
        using it (connector-proxy returns an error).
      </p>
      <div className="mgr-list">
        {items.map((c) => {
          const expanded = open === c.provider;
          return (
            <div className={`mgr-card ${c.enabled ? "" : "is-off"}`} key={c.provider}>
              <div className="mgr-card-main">
                <div className="mgr-card-title">
                  <span className="mgr-conn-label">{c.label}</span>
                  <code className="mgr-slug">{c.provider}</code>
                  <span className="mgr-badge soft">{c.transport}</span>
                  {!c.enabled && <span className="mgr-badge off">disabled</span>}
                </div>
                {c.tools.length > 0 ? (
                  <button className="mgr-tools-toggle" onClick={() => setOpen(expanded ? null : c.provider)}>
                    {expanded ? "▾" : "▸"} {c.tools.length} tool{c.tools.length === 1 ? "" : "s"}
                  </button>
                ) : (
                  <p className="mgr-card-when">{c.agent_hint || "Tools discovered at runtime."}</p>
                )}
                {expanded && (
                  <div className="mgr-tools">
                    {c.tools.map((t) => (
                      <div className="mgr-tool" key={t.name}>
                        <code>{t.name}</code>
                        <span>{t.description}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="mgr-card-actions">
                <button
                  className={`mgr-switch ${c.enabled ? "on" : ""}`}
                  onClick={() => toggle(c)}
                  title={c.enabled ? "Disable" : "Enable"}
                  aria-label="Toggle enabled"
                >
                  <span />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ----------------------------------------------------------------------------- //
// Manager (admin)
// ----------------------------------------------------------------------------- //
export function ManagerView() {
  const [tab, setTab] = useState<Tab>("skills");
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    fetch("/api/admin/skills").then((r) => {
      if (r.status === 403 || r.status === 401) setForbidden(true);
    });
  }, []);

  if (forbidden) {
    return (
      <div className="page">
        <header className="page-head">
          <h1>Manager</h1>
        </header>
        <p className="muted-note">Admins only.</p>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-head">
        <h1>Manager</h1>
        <p className="page-sub">Agent skills and connector/MCP availability.</p>
      </header>

      <div className="mgr-tabs">
        <button className={`mgr-tab ${tab === "skills" ? "active" : ""}`} onClick={() => setTab("skills")}>
          Skills
        </button>
        <button
          className={`mgr-tab ${tab === "connectors" ? "active" : ""}`}
          onClick={() => setTab("connectors")}
        >
          Connectors / Tools
        </button>
      </div>

      {tab === "skills" ? <SkillsTab /> : <ConnectorsTab />}
    </div>
  );
}
