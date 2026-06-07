import { Fragment, useEffect, useRef, useState } from "react";
import { getToken } from "../lib/auth";

/**
 * Overlay que aparece sobre el Chat Builder cuando la app está desplegando/construyendo.
 * Se suscribe al SSE de progreso (`/api/apps/{id}/deploy/stream`, que emite el stage actual
 * al conectar — útil al entrar tarde) y muestra un STEPPER de fases + la etapa en vivo.
 * Al terminar (done/deployed/error/cancelled) llama `onDone` para refrescar la app.
 */

// Macro-fases del pipeline (iguales para build desde cero o edición). El texto libre del stage
// se mapea a una de estas por keywords (ES/EN), así el usuario ve el proceso completo.
const PHASES = [
  { id: "generate", label: "Generate" },
  { id: "qa", label: "Test" },
  { id: "deploy", label: "Deploy" },
] as const;

function phaseIndex(text: string): number {
  const t = text.toLowerCase();
  if (/(deploy|desplegando|scheduling|programando|tareas|tasks)/.test(t)) return 2;
  if (/(qa|test|building|fixing|depend|applying ui|aplicando)/.test(t)) return 1;
  return 0; // generate / edit / reuse / database
}

export function DeployingOverlay({
  appId,
  title,
  onDone,
}: {
  appId: string;
  title: string;
  onDone: () => void;
}) {
  const [current, setCurrent] = useState("Starting…");
  const [maxPhase, setMaxPhase] = useState(0);
  const [failed, setFailed] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setCurrent("Starting…");
    setMaxPhase(0);
    setFailed(false);
    const es = new EventSource(
      `/api/apps/${appId}/deploy/stream?token=${encodeURIComponent(getToken() ?? "")}`,
    );
    esRef.current = es;
    es.onmessage = (e) => {
      let evt: { type?: string; text?: string };
      try {
        evt = JSON.parse(e.data);
      } catch {
        return;
      }
      if (evt.type === "stage" && evt.text) {
        const text = evt.text;
        setCurrent(text);
        setMaxPhase((p) => Math.max(p, phaseIndex(text)));
      } else if (evt.type && ["done", "deployed", "error", "cancelled"].includes(evt.type)) {
        if (evt.type === "error") setFailed(true);
        else setMaxPhase(PHASES.length); // todas las fases completas
        es.close();
        setTimeout(onDone, evt.type === "error" ? 1200 : 500);
      }
    };
    es.onerror = () => {
      /* EventSource reconecta solo; el poll de AgentsView cubre el cierre del estado. */
    };
    return () => es.close();
  }, [appId, onDone]);

  return (
    <div className="deploy-overlay" role="status" aria-live="polite">
      <div className="deploy-overlay-card">
        <h3 className="deploy-overlay-title">
          {failed ? "Deploy failed" : "Deploying"}{" "}
          <span className="deploy-overlay-app">{title}</span>
        </h3>

        <div className="deploy-stepper" aria-hidden="true">
          {PHASES.map((p, i) => {
            const state = failed && i === maxPhase
              ? "failed"
              : i < maxPhase
                ? "done"
                : i === maxPhase
                  ? "active"
                  : "pending";
            return (
              <Fragment key={p.id}>
                {i > 0 && (
                  <div className={`deploy-step-line ${i <= maxPhase ? "filled" : ""}`} />
                )}
                <div className={`deploy-step ${state}`}>
                  <span className="deploy-step-dot">
                    {state === "done" ? "✓" : state === "failed" ? "!" : i + 1}
                  </span>
                  <span className="deploy-step-label">{p.label}</span>
                </div>
              </Fragment>
            );
          })}
        </div>

        <p className={`deploy-overlay-current ${failed ? "failed" : ""}`}>{current}</p>
        <p className="deploy-overlay-hint">
          Building and testing your app. This usually takes about a minute.
        </p>
      </div>
    </div>
  );
}
