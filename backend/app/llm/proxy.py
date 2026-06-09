"""Proxy LLM unificado para las apps desplegadas.

Las apps NUNCA reciben las API keys: llaman al gateway de la plataforma con su
`X-App-Secret` y este módulo —que tiene las claves en el entorno— hace la llamada al
proveedor y devuelve un resultado normalizado. Enruta por el nombre del modelo:
  claude*  -> Anthropic   ·   gpt*  -> OpenAI   ·   gemini*  -> Google Gemini

Formato de entrada (común a los 3 proveedores):
  messages = [{"role": "user"|"assistant", "content": <str> | [parte, ...]}]
  parte    = {"type": "text", "text": "..."}
           | {"type": "image", "mime": "image/png", "data": "<base64>"}
           | {"type": "document", "mime": "application/pdf", "data": "<base64>"}
  system   = "..."  (opcional)

Salida: {"text", "provider", "model", "usage": {"input_tokens", "output_tokens"}}
  Los modelos de imagen de Gemini (Nano Banana) agregan además:
    "images": [{"mime": "image/png", "data": "<base64>"}]  (una o más imágenes generadas/editadas)
"""
from __future__ import annotations

from typing import Any

import httpx

from ..core.config import settings

# Allowlist de modelos -> proveedor. El primero de cada grupo es el "barato" recomendado.
MODELS: dict[str, str] = {
    # Anthropic
    "claude-haiku-4-5": "anthropic",
    "claude-sonnet-4-6": "anthropic",
    "claude-opus-4-8": "anthropic",
    # OpenAI
    "gpt-4o-mini": "openai",
    "gpt-4o": "openai",
    # Google Gemini (texto / multimodal de entrada)
    "gemini-2.5-flash": "google",
    "gemini-2.5-pro": "google",
    # Google Gemini — generación y edición de IMÁGENES (Nano Banana). Devuelven `images`.
    "gemini-2.5-flash-image": "google",   # Nano Banana
    "gemini-3.1-flash-image": "google",   # Nano Banana 2
    "gemini-3-pro-image": "google",       # Nano Banana Pro (4K, reasoning)
}
DEFAULT_MODEL = "claude-haiku-4-5"

# Modelos de imagen de Gemini: requieren pedir la modalidad IMAGE y devuelven la imagen en
# las `parts` (inlineData). Se enrutan al mismo proveedor "google" pero con manejo distinto.
GEMINI_IMAGE_MODELS: set[str] = {
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
}


class LLMError(RuntimeError):
    pass


def _norm_content(content: Any) -> list[dict[str, Any]]:
    """Normaliza el `content` de un mensaje a una lista de partes con 'type'."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


# ---- Anthropic -----------------------------------------------------------------

def _anthropic_blocks(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            out.append({"type": "text", "text": p.get("text", "")})
        elif t == "image":
            out.append({"type": "image", "source": {
                "type": "base64", "media_type": p.get("mime", "image/png"), "data": p["data"]}})
        elif t == "document":
            out.append({"type": "document", "source": {
                "type": "base64", "media_type": p.get("mime", "application/pdf"), "data": p["data"]}})
    return out


async def _call_anthropic(model, messages, system, max_tokens, temperature) -> dict:
    from anthropic import AsyncAnthropic

    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY no configurada.")
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": m.get("role", "user"), "content": _anthropic_blocks(_norm_content(m.get("content")))}
                for m in messages
            ],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = await client.messages.create(**kwargs)
    finally:
        await client.close()
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return {
        "text": text,
        "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
    }


# ---- OpenAI --------------------------------------------------------------------

def _openai_content(parts: list[dict[str, Any]]) -> Any:
    # Si es solo texto, devolvemos string (formato simple).
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0].get("text", "")
    out: list[dict[str, Any]] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            out.append({"type": "text", "text": p.get("text", "")})
        elif t == "image":
            url = f"data:{p.get('mime', 'image/png')};base64,{p['data']}"
            out.append({"type": "image_url", "image_url": {"url": url}})
        elif t == "document":
            raise LLMError("OpenAI no acepta PDF directo: usá un modelo claude-* o gemini-* para documentos.")
    return out


async def _call_openai(model, messages, system, max_tokens, temperature) -> dict:
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY no configurada.")
    msgs: list[dict[str, Any]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    for m in messages:
        msgs.append({"role": m.get("role", "user"), "content": _openai_content(_norm_content(m.get("content")))})
    body: dict[str, Any] = {"model": model, "messages": msgs, "max_tokens": max_tokens}
    if temperature is not None:
        body["temperature"] = temperature
    async with httpx.AsyncClient(timeout=90.0) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"}, json=body,
        )
    if r.status_code >= 400:
        raise LLMError(f"OpenAI {r.status_code}: {r.text[:300]}")
    data = r.json()
    usage = data.get("usage", {})
    return {
        "text": data["choices"][0]["message"]["content"],
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ---- Google Gemini -------------------------------------------------------------

def _gemini_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            out.append({"text": p.get("text", "")})
        elif t in ("image", "document"):
            out.append({"inline_data": {"mime_type": p.get("mime", "application/octet-stream"), "data": p["data"]}})
    return out


async def _call_gemini(model, messages, system, max_tokens, temperature) -> dict:
    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY no configurada.")
    is_image = model in GEMINI_IMAGE_MODELS
    contents = [
        {"role": "model" if m.get("role") == "assistant" else "user",
         "parts": _gemini_parts(_norm_content(m.get("content")))}
        for m in messages
    ]
    gen_cfg: dict[str, Any] = {}
    if temperature is not None:
        gen_cfg["temperature"] = temperature
    body: dict[str, Any] = {"contents": contents, "generationConfig": gen_cfg}
    if is_image:
        # Nano Banana: hay que pedir EXPLÍCITAMENTE la modalidad IMAGE (si no, no devuelve imagen).
        # Estos modelos no usan systemInstruction: el system va como una parte de texto al inicio.
        gen_cfg["responseModalities"] = ["TEXT", "IMAGE"]
        if system:
            contents.insert(0, {"role": "user", "parts": [{"text": system}]})
    else:
        gen_cfg["maxOutputTokens"] = max_tokens
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    # La generación de imagen tarda más que una respuesta de texto: damos más margen.
    async with httpx.AsyncClient(timeout=180.0 if is_image else 90.0) as c:
        r = await c.post(url, params={"key": settings.gemini_api_key}, json=body)
    if r.status_code >= 400:
        raise LLMError(f"Gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    cands = data.get("candidates", [])
    text = ""
    images: list[dict[str, str]] = []
    if cands:
        for p in cands[0].get("content", {}).get("parts", []):
            if p.get("text"):
                text += p["text"]
            # En las respuestas REST v1beta la imagen llega como inlineData (camelCase).
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                images.append({
                    "mime": inline.get("mimeType") or inline.get("mime_type") or "image/png",
                    "data": inline["data"],
                })
    if is_image and not images:
        # Sin imagen: suele ser un bloqueo por seguridad. Mostramos el motivo si lo hay.
        reason = (cands[0].get("finishReason") if cands else None) or "sin imagen en la respuesta"
        raise LLMError(f"Gemini no devolvió imagen ({reason}). {text[:200]}".strip())
    um = data.get("usageMetadata", {})
    result: dict[str, Any] = {
        "text": text,
        "usage": {
            "input_tokens": um.get("promptTokenCount", 0),
            "output_tokens": um.get("candidatesTokenCount", 0),
        },
    }
    if images:
        result["images"] = images
    return result


_ROUTER = {"anthropic": _call_anthropic, "openai": _call_openai, "google": _call_gemini}


async def complete(
    model: str,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Llama al proveedor del `model` (validado contra la allowlist) y normaliza la salida."""
    provider = MODELS.get(model)
    if provider is None:
        raise LLMError(
            f"Modelo '{model}' no permitido. Disponibles: {', '.join(MODELS)}."
        )
    if not messages:
        raise LLMError("Faltan mensajes.")
    max_tokens = max(1, min(int(max_tokens or 1024), 8192))
    result = await _ROUTER[provider](model, messages, system, max_tokens, temperature)
    result["provider"] = provider
    result["model"] = model
    return result
