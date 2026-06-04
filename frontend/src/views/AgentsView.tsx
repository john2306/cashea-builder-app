import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
} from "react";
import { AttachmentChip } from "../components/AttachmentChip";
import { DeployControl } from "../components/DeployControl";
import { DeployDialog } from "../components/DeployDialog";
import { SpecReviewDialog } from "../components/SpecReviewDialog";
import { MessageBubble } from "../components/MessageBubble";
import { ModelSelect } from "../components/ModelSelect";
import { useAgentSocket } from "../hooks/useAgentSocket";
import { hydrateMessages } from "../lib/conversation";
import { fileToAttachment } from "../lib/files";
import { getStoredModel, setStoredModel } from "../lib/models";
import type { AppProject, Attachment, ConversationDetail } from "../types";

const SAMPLE_PROMPTS = [
  "Classify my emails and if they say 'invoice' process the attachment and save it to Sheets",
  "When I'm mentioned in Slack, summarize the thread and create it in Notion",
  "Receive a webhook, check a condition and notify via Slack",
];

function PaperclipIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
      <path d="M8.7 12.8l5.8-5.8a3 3 0 014.2 4.2l-7.5 7.5a5 5 0 01-7.1-7.1l8.3-8.3a6.8 6.8 0 019.6 9.6l-8.2 8.2" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
      <path d="M4 12L20 4l-4 16-3.5-6.5L4 12z" />
      <path d="M20 4l-7.5 9.5" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
      <path d="M4 20h4l10-10-4-4L4 16v4z" />
      <path d="M14 6l4 4" />
    </svg>
  );
}

async function apiJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function AgentsView({ openAppId }: { openAppId?: string | null }) {
  const { messages, connected, running, appId, send, cancel, loadConversation } = useAgentSocket();
  const [input, setInput] = useState("");
  const [model, setModel] = useState<string>(getStoredModel);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, setApps] = useState<AppProject[]>([]);
  const [selectedApp, setSelectedApp] = useState<AppProject | null>(null);
  const [pendingDeploy, setPendingDeploy] = useState<AppProject | null>(null);
  const [pendingReview, setPendingReview] = useState<AppProject | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // "Pegado al fondo": solo auto-scrolleamos si el usuario YA está cerca del fondo. Si subió
  // a leer, no lo arrastramos hacia abajo en cada actualización del stream.
  const stickRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const mergeApp = useCallback((updated: AppProject) => {
    setSelectedApp((cur) => (cur?.id === updated.id ? { ...cur, ...updated } : cur));
    setApps((cur) => cur.map((a) => (a.id === updated.id ? { ...a, ...updated } : a)));
  }, []);

  const refreshApps = useCallback(async () => {
    const page = await apiJson<{ items: AppProject[] }>("/api/apps?limit=100");
    const rows = page.items ?? [];
    setApps(rows);
    return rows;
  }, []);

  const persistApp = useCallback(
    async (appProjectId: string, payload: Partial<Pick<AppProject, "title" | "status">>) => {
      const updated = await apiJson<AppProject>(`/api/apps/${appProjectId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      mergeApp(updated);
      return updated;
    },
    [mergeApp],
  );

  const selectApp = useCallback(
    async (nextAppId: string) => {
      const appProject = await apiJson<AppProject>(`/api/apps/${nextAppId}`);
      setSelectedApp(appProject);
      const conversation = await apiJson<ConversationDetail>(
        `/api/conversations/${appProject.conversation_id}`,
      );
      loadConversation(
        appProject.conversation_id,
        hydrateMessages(conversation.messages),
        appProject.id,
      );
    },
    [loadConversation],
  );

  const createApp = useCallback(async () => {
    const title = input.trim() || "New agent";
    const appProject = await apiJson<AppProject>("/api/apps", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    setSelectedApp(appProject);
    loadConversation(appProject.conversation_id, [], appProject.id);
    await refreshApps();
    return appProject;
  }, [input, loadConversation, refreshApps]);

  const ensureSelectedApp = useCallback(async () => {
    if (selectedApp) return selectedApp;
    return createApp();
  }, [createApp, selectedApp]);

  const renameApp = useCallback(
    (id: string, title: string) => {
      persistApp(id, { title }).catch(() => setError("Could not rename the agent."));
    },
    [persistApp],
  );

  // Abre el modal de subdominio (la app debe existir primero).
  const openDeploy = useCallback(async () => {
    setError(null);
    try {
      setPendingDeploy(await ensureSelectedApp());
    } catch {
      setError("Could not prepare the deployment.");
    }
  }, [ensureSelectedApp]);

  useEffect(() => {
    refreshApps()
      .then((rows) => {
        // Si venimos de "Construir" en Apps, abrimos esa app; si no, la primera.
        if (openAppId && rows.some((a) => a.id === openAppId)) {
          selectApp(openAppId);
        } else if (rows[0]) {
          selectApp(rows[0].id);
        }
      })
      .catch(() => setError("Could not load the agents."));
  }, [refreshApps, selectApp, openAppId]);

  useEffect(() => {
    if (!appId || selectedApp?.id === appId) return;
    apiJson<AppProject>(`/api/apps/${appId}`)
      .then((appProject) => {
        setSelectedApp(appProject);
        refreshApps();
      })
      .catch(() => undefined);
  }, [appId, refreshApps, selectedApp?.id]);

  useEffect(() => {
    if (stickRef.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
      setShowJump(false);
    } else {
      setShowJump(true); // llegó contenido nuevo mientras el usuario lee más arriba
    }
  }, [messages]);

  // Recalcula si estamos pegados al fondo (umbral 80px) y si mostrar la flecha "ir al final".
  const onMessagesScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickRef.current = dist < 80;
    setShowJump(dist > 160);
  }, []);

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    stickRef.current = true;
    setShowJump(false);
  }, []);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  useEffect(() => {
    if (selectedApp?.deploy_state !== "deploying") return;
    const id = selectedApp.id;
    const timer = setInterval(() => {
      apiJson<AppProject>(`/api/apps/${id}`).then(mergeApp).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [mergeApp, selectedApp?.deploy_state, selectedApp?.id]);

  const addFiles = async (files: FileList | File[]) => {
    setError(null);
    const list = Array.from(files);
    const results = await Promise.allSettled(list.map(fileToAttachment));
    const ok: Attachment[] = [];
    const errors: string[] = [];
    for (const r of results) {
      if (r.status === "fulfilled") ok.push(r.value);
      else errors.push(r.reason?.message ?? "Error reading a file");
    }
    if (ok.length) setAttachments((prev) => [...prev, ...ok]);
    if (errors.length) setError(errors.join(" "));
  };

  const onPickFiles = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files);
    e.target.value = "";
  };

  // Pegar desde el portapapeles (Ctrl/Cmd+V): screenshots, imágenes y archivos copiados.
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const dt = e.clipboardData;
    if (!dt) return;
    const files: File[] = dt.files?.length
      ? Array.from(dt.files)
      : Array.from(dt.items)
          .filter((it) => it.kind === "file")
          .map((it) => it.getAsFile())
          .filter((f): f is File => f !== null);
    if (!files.length) return; // texto normal: dejamos el pegado por defecto
    e.preventDefault();
    // Los screenshots suelen llegar sin nombre: les damos uno legible.
    const named = files.map((f) =>
      f.name
        ? f
        : new File([f], `pasted-${Date.now()}.${f.type.split("/")[1] || "png"}`, {
            type: f.type || "image/png",
          }),
    );
    addFiles(named);
  };

  const removeAttachment = (idx: number) =>
    setAttachments((prev) => prev.filter((_, i) => i !== idx));

  const submit = () => {
    const text = input.trim();
    if ((!text && attachments.length === 0) || running) return;
    send(text, attachments, model);
    setInput("");
    setAttachments([]);
  };

  const canSend = connected && !running && Boolean(input.trim() || attachments.length);
  const appTitle = selectedApp?.title ?? "New agent";

  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const startEditName = () => {
    setNameDraft(appTitle);
    setEditingName(true);
  };
  const saveName = () => {
    setEditingName(false);
    const t = nameDraft.trim();
    if (selectedApp && t && t !== selectedApp.title) renameApp(selectedApp.id, t);
  };

  return (
    <div className="app-shell">
      <section className="chat-panel" aria-label="Agent builder">
        <header className="chat-header">
          <div className="chat-title">
            <p className="eyebrow">Builder Agent</p>
            {editingName ? (
              <input
                className="app-name-input"
                value={nameDraft}
                autoFocus
                maxLength={120}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={saveName}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    saveName();
                  }
                  if (e.key === "Escape") setEditingName(false);
                }}
              />
            ) : (
              <h2 className="app-name">
                {appTitle}
                {selectedApp && (
                  <button
                    className="app-name-edit tip tip-bottom"
                    type="button"
                    data-tooltip="Edit name"
                    aria-label="Edit name"
                    onClick={startEditName}
                  >
                    <PencilIcon />
                  </button>
                )}
              </h2>
            )}
          </div>
          <div className="chat-actions">
            <ModelSelect
              value={model}
              onChange={(m) => {
                setModel(m);
                setStoredModel(m);
              }}
              disabled={running}
            />
            {selectedApp && (
              <button
                className="chat-config-btn tip tip-bottom"
                type="button"
                onClick={() => setPendingReview(selectedApp)}
                data-tooltip="App settings"
                aria-label="App settings"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 6h16M4 12h16M4 18h10" />
                </svg>
              </button>
            )}
            <DeployControl app={selectedApp} onDeploy={openDeploy} />
            <span
              className={`status-dot tip tip-bottom tip-end ${connected ? "online" : "offline"}`}
              data-tooltip={connected ? "Connected" : "Reconnecting…"}
              role="status"
              aria-label={connected ? "Connected" : "Reconnecting"}
            />
          </div>
        </header>

        <main
          className={`messages ${dragging ? "dragging" : ""}`}
          ref={scrollRef}
          onScroll={onMessagesScroll}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
          }}
        >
          {messages.length === 0 && (
            <div className="empty">
              <div className="empty-mark" aria-hidden="true" />
              <h3>{"What agent are we building today?"}</h3>
              <div className="prompt-grid" aria-label="Quick ideas">
                {SAMPLE_PROMPTS.map((prompt) => (
                  <button
                    className="prompt-chip"
                    key={prompt}
                    type="button"
                    onClick={() => setInput(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {running && (
            <div className="bubble-row from-assistant typing-row">
              <div className="bubble-avatar assistant-avatar" aria-hidden="true" />
              <span className="typing">{"The agent is working…"}</span>
            </div>
          )}
          {dragging && <div className="drop-hint">{"Drop the files here"}</div>}
          {showJump && (
            <button
              className="chat-jump"
              type="button"
              onClick={jumpToBottom}
              aria-label="Jump to bottom"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
          )}
        </main>

        <footer className="composer">
          {error && <div className="composer-error">{error}</div>}

          {attachments.length > 0 && (
            <div className="chips">
              {attachments.map((a, i) => (
                <AttachmentChip
                  key={i}
                  att={{ name: a.name, kind: a.kind, size: a.size }}
                  onRemove={() => removeAttachment(i)}
                />
              ))}
            </div>
          )}

          <div className="composer-row">
            <button
              className="attach-btn tip tip-top tip-left"
              onClick={() => fileInputRef.current?.click()}
              disabled={!connected}
              data-tooltip="Attach files"
              aria-label="Attach files"
              type="button"
            >
              <PaperclipIcon />
            </button>
            <input ref={fileInputRef} type="file" multiple hidden onChange={onPickFiles} />
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              onPaste={onPaste}
              placeholder={`Describe the agent you want to build…`}
              rows={1}
              disabled={!connected}
            />
            {running ? (
              <button className="send-btn stop-btn" onClick={cancel} type="button">
                <span>Stop</span>
                <span className="stop-glyph" aria-hidden="true" />
              </button>
            ) : (
              <button className="send-btn" onClick={submit} disabled={!canSend} type="button">
                <span>Send</span>
                <SendIcon />
              </button>
            )}
          </div>
        </footer>
      </section>

      {pendingReview && (
        <SpecReviewDialog
          appId={pendingReview.id}
          appTitle={pendingReview.title}
          onClose={() => setPendingReview(null)}
        />
      )}

      {pendingDeploy && (
        <DeployDialog
          app={pendingDeploy}
          onClose={() => setPendingDeploy(null)}
          onDeployed={() => mergeApp({ ...pendingDeploy, deploy_state: "deploying" })}
        />
      )}
    </div>
  );
}
