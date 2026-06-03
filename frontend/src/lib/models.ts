import type { AppStatus } from "../types";

export interface ModelOption {
  id: string;
  label: string;
  hint: string;
}

// Modelos disponibles (ver https://platform.claude.com/docs/en/about-claude/models/overview).
// El backend valida contra esta misma lista (AVAILABLE_MODELS en runner.py).
export const MODELS: ModelOption[] = [
  { id: "claude-opus-4-8", label: "Opus 4.8", hint: "Máxima capacidad" },
  { id: "claude-opus-4-7", label: "Opus 4.7", hint: "Recomendado" },
  { id: "claude-opus-4-6", label: "Opus 4.6", hint: "Opus anterior" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6", hint: "Equilibrio velocidad/calidad" },
  { id: "claude-haiku-4-5", label: "Haiku 4.5", hint: "Rápido y económico" },
];

export const DEFAULT_MODEL = "claude-opus-4-7";

// Etiquetas y colores de los estados de una app.
export const STATUS_META: Record<AppStatus, { label: string; color: string }> = {
  draft: { label: "Draft", color: "var(--muted)" },
  testing: { label: "Testing", color: "var(--warning)" },
  production: { label: "Production", color: "var(--ok)" },
};

export const STATUS_ORDER: AppStatus[] = ["draft", "testing", "production"];
