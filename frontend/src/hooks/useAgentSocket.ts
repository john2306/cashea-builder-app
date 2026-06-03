import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../lib/auth";
import type { Attachment, ChatMessage, ServerEvent, ToolCall } from "../types";

// Transporte: POST inicia el run (en background, publica a Redis) y un EventSource (SSE)
// streamea los eventos. El EventSource reconecta solo y reanuda con Last-Event-ID, así que
// una caída de red NO pierde el run (el run vive en el server, independiente del cliente).

let idCounter = 0;
const nextId = () => `m${Date.now()}-${idCounter++}`;

const streamUrl = (runId: string) =>
  `/api/agent/runs/${runId}/stream?token=${encodeURIComponent(getToken() ?? "")}`;

export function useAgentSocket() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [connected, setConnected] = useState(true);
  const [running, setRunning] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [appId, setAppId] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const activeAssistantRef = useRef<string | null>(null);
  const runIdRef = useRef<string | null>(null);

  const patchActive = useCallback((fn: (m: ChatMessage) => ChatMessage) => {
    const activeId = activeAssistantRef.current;
    if (!activeId) return;
    setMessages((prev) => prev.map((m) => (m.id === activeId ? fn(m) : m)));
  }, []);

  const patchTool = useCallback(
    (toolId: string, fn: (t: ToolCall) => ToolCall) => {
      patchActive((m) => ({ ...m, tools: m.tools.map((t) => (t.id === toolId ? fn(t) : t)) }));
    },
    [patchActive],
  );

  // Al terminar/cancelar el run, cerramos las tarjetas de tools que quedaron "cargando"
  // (tool_use sin su tool_result) para que no giren para siempre.
  const finalizePending = useCallback(() => {
    patchActive((m) => ({
      ...m,
      tools: m.tools.map((t) =>
        t.result === null
          ? { ...t, result: "(ejecución finalizada sin resultado)", isError: true, progress: 1 }
          : t,
      ),
    }));
  }, [patchActive]);

  const handleEvent = useCallback(
    (ev: ServerEvent) => {
      switch (ev.type) {
        case "conversation":
          conversationIdRef.current = ev.conversation_id;
          setConversationId(ev.conversation_id);
          break;
        case "app":
          setAppId(ev.app_id);
          break;
        case "status":
          if (ev.state === "running") {
            setRunning(true);
            if (!activeAssistantRef.current) {
              const id = nextId();
              activeAssistantRef.current = id;
              setMessages((prev) => [
                ...prev,
                { id, role: "assistant", text: "", thinking: "", tools: [] },
              ]);
            }
          } else {
            setRunning(false);
            activeAssistantRef.current = null;
          }
          break;
        case "token":
          patchActive((m) => ({ ...m, text: m.text + ev.text }));
          break;
        case "thinking":
          patchActive((m) => ({ ...m, thinking: m.thinking + ev.text }));
          break;
        case "tool_use":
          patchActive((m) => ({
            ...m,
            tools: [
              ...m.tools,
              {
                id: ev.tool_use_id,
                name: ev.name,
                input: ev.input,
                longRunning: ev.long_running,
                progress: null,
                progressMessage: null,
                result: null,
                isError: false,
              },
            ],
          }));
          break;
        case "tool_progress":
          patchTool(ev.tool_use_id, (t) => ({
            ...t,
            progress: ev.progress,
            progressMessage: ev.message,
          }));
          break;
        case "tool_result":
          patchTool(ev.tool_use_id, (t) => ({
            ...t,
            result: ev.content,
            isError: ev.is_error,
            progress: ev.is_error ? t.progress : 1,
          }));
          break;
        case "message_done":
          patchActive((m) => ({ ...m, usage: ev.usage }));
          break;
        case "error":
          patchActive((m) => ({ ...m, text: m.text + `\n\n⚠️ Error: ${ev.message}` }));
          finalizePending();
          setRunning(false);
          activeAssistantRef.current = null;
          break;
      }
    },
    [patchActive, patchTool, finalizePending],
  );

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  // Abre (o reabre) el SSE de un run. El navegador reconecta solo con Last-Event-ID.
  const openStream = useCallback(
    (runId: string) => {
      runIdRef.current = runId;
      closeStream();
      const es = new EventSource(streamUrl(runId));
      esRef.current = es;
      es.onopen = () => setConnected(true);
      es.onmessage = (e) => {
        let ev: ServerEvent;
        try {
          ev = JSON.parse(e.data) as ServerEvent;
        } catch {
          return;
        }
        if (ev.type === "end") {
          // Run terminado: cerramos para que el navegador NO reconecte.
          finalizePending();
          closeStream();
          setRunning(false);
          activeAssistantRef.current = null;
          return;
        }
        handleEvent(ev);
      };
      es.onerror = () => {
        // Caída transitoria: el EventSource reintenta solo (con Last-Event-ID). Solo
        // reflejamos el estado de conexión.
        setConnected(false);
      };
    },
    [closeStream, handleEvent, finalizePending],
  );

  useEffect(() => () => closeStream(), [closeStream]);

  const send = useCallback(
    async (content: string, attachments: Attachment[] = [], model?: string) => {
      setRunning(true);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "user",
          text: content,
          thinking: "",
          tools: [],
          attachments: attachments.map((a) => ({ name: a.name, kind: a.kind, size: a.size })),
        },
      ]);
      try {
        const res = await fetch("/api/agent/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content,
            attachments,
            model,
            conversation_id: conversationIdRef.current,
          }),
        });
        if (!res.ok) throw new Error(`run ${res.status}`);
        const data = (await res.json()) as {
          run_id: string;
          conversation_id: string;
          app_id: string;
        };
        conversationIdRef.current = data.conversation_id;
        setConversationId(data.conversation_id);
        if (data.app_id) setAppId(data.app_id);
        setConnected(true);
        openStream(data.run_id);
      } catch {
        setRunning(false);
        setConnected(false);
        patchActive((m) => ({
          ...m,
          text: m.text + "\n\n⚠️ No se pudo iniciar el run. Reintentá.",
        }));
      }
    },
    [openStream, patchActive],
  );

  // Detiene el run en curso (corta loops). El stream recibirá los terminales y cerrará.
  const cancel = useCallback(() => {
    const runId = runIdRef.current;
    if (!runId) return;
    setRunning(false);
    fetch(`/api/agent/runs/${runId}/cancel`, { method: "POST" }).catch(() => undefined);
  }, []);

  const loadConversation = useCallback(
    (nextConversationId: string, nextMessages: ChatMessage[], nextAppId?: string) => {
      closeStream();
      conversationIdRef.current = nextConversationId;
      activeAssistantRef.current = null;
      setConversationId(nextConversationId);
      setMessages(nextMessages);
      setRunning(false);
      if (nextAppId) setAppId(nextAppId);
      // Si hay un run en curso para esta conversación (p. ej. recargaste la página), reanudá.
      fetch(`/api/agent/active-run?conversation_id=${encodeURIComponent(nextConversationId)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (d?.run_id) {
            setRunning(true);
            openStream(d.run_id);
          }
        })
        .catch(() => undefined);
    },
    [closeStream, openStream],
  );

  return {
    messages,
    connected,
    running,
    conversationId,
    appId,
    send,
    cancel,
    loadConversation,
  };
}
