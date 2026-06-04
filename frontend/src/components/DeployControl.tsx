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
        Deploying…
      </span>
    );
  }

  if (state === "deployed" && app?.url) {
    return (
      <div className="deploy-group">
        <a
          className="deploy-link tip tip-bottom"
          href={app.url}
          target="_blank"
          rel="noopener noreferrer"
          data-tooltip="Open app in a new tab"
        >
          <span className="deploy-dot" aria-hidden="true" />
          <span className="deploy-host">{app.url.replace(/^https?:\/\//, "")}</span>
          <svg className="deploy-ext" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
          </svg>
        </a>
        <button
          className="deploy-btn ghost tip tip-bottom tip-end"
          type="button"
          onClick={onDeploy}
          data-tooltip="Rebuild and redeploy"
        >
          <svg className="deploy-ic" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
          </svg>
          Redeploy
        </button>
      </div>
    );
  }

  if (state === "error") {
    return (
      <button className="deploy-btn danger" type="button" onClick={onDeploy}>
        Retry deploy
      </button>
    );
  }

  return (
    <button className="deploy-btn" type="button" onClick={onDeploy}>
      <span className="deploy-rocket" aria-hidden="true" />
      Deploy
    </button>
  );
}
