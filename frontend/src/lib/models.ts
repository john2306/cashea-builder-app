import type { AppStatus } from "../types";

export interface ModelOption {
  id: string;
  label: string;
  hint: string;
}

// Modelos disponibles (ver https://platform.claude.com/docs/en/about-claude/models/overview).
// El backend valida contra esta misma lista (AVAILABLE_MODELS en runner.py).
export const MODELS: ModelOption[] = [
  { id: "claude-opus-4-8", label: "Opus 4.8", hint: "Top capability" },
  { id: "claude-opus-4-7", label: "Opus 4.7", hint: "Recommended" },
  { id: "claude-opus-4-6", label: "Opus 4.6", hint: "Previous Opus" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6", hint: "Speed/quality balance" },
];

export const DEFAULT_MODEL = "claude-opus-4-7";

// Persistencia de la preferencia de modelo del Builder (por navegador).
const MODEL_KEY = "cashea_hub_model";

export function getStoredModel(): string {
  try {
    const v = localStorage.getItem(MODEL_KEY);
    if (v && MODELS.some((m) => m.id === v)) return v;
  } catch {
    /* ignore */
  }
  return DEFAULT_MODEL;
}

export function setStoredModel(id: string): void {
  try {
    localStorage.setItem(MODEL_KEY, id);
  } catch {
    /* ignore */
  }
}

// Etiquetas y colores de los estados de una app.
export const STATUS_META: Record<AppStatus, { label: string; color: string }> = {
  draft: { label: "Draft", color: "var(--muted)" },
  testing: { label: "Testing", color: "var(--warning)" },
  production: { label: "Production", color: "var(--ok)" },
};

export const STATUS_ORDER: AppStatus[] = ["draft", "testing", "production"];
