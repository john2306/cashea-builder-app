import type { AppProject } from "../types";

export function DeployControl({
  app,
  onDeploy,
}: {
  app: AppProject | null;
  onDeploy: () => void;
}) {
  const state = app?.deploy_state ?? "idle";

  if (state === "deploying") {
    return (
      <span className="deploy-pill deploying">
        <span className="deploy-spinner" aria-hidden="true" />
        Desplegando…
      </span>
    );
  }

  if (state === "deployed" && app?.url) {
    return (
      <div className="deploy-group">
        <a className="deploy-link" href={app.url} target="_blank" rel="noopener noreferrer">
          <span className="deploy-dot" aria-hidden="true" />
          {app.url.replace(/^https?:\/\//, "")}
        </a>
        <button className="deploy-btn ghost" type="button" onClick={onDeploy}>
          Actualizar
        </button>
      </div>
    );
  }

  if (state === "error") {
    return (
      <button className="deploy-btn danger" type="button" onClick={onDeploy}>
        Reintentar deploy
      </button>
    );
  }

  return (
    <button className="deploy-btn" type="button" onClick={onDeploy}>
      <span className="deploy-rocket" aria-hidden="true" />
      Desplegar
    </button>
  );
}
