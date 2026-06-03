// Sesión del builder: login con Google vía gateway (/auth/google/*).
// El callback redirige a <origin>#token=<jwt>; lo guardamos en localStorage y leemos
// el usuario decodificando el payload del JWT (la verificación de firma vive en el backend).

const TOKEN_KEY = "cashea_hub_session";

export interface SessionUser {
  sub?: string;
  email?: string;
  name?: string;
  picture?: string;
  is_admin?: boolean;
  exp?: number;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function decodePayload(token: string): SessionUser | null {
  try {
    const seg = token.split(".")[1];
    const b64 = seg.replace(/-/g, "+").replace(/_/g, "/");
    // atob + reconstrucción UTF-8 (nombres con acentos/ñ).
    const json = decodeURIComponent(
      Array.prototype.map
        .call(atob(b64), (c: string) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join(""),
    );
    return JSON.parse(json) as SessionUser;
  } catch {
    return null;
  }
}

/** Usuario de la sesión actual, o null si no hay token o está vencido. */
export function currentUser(): SessionUser | null {
  const token = getToken();
  if (!token) return null;
  const user = decodePayload(token);
  if (!user || (user.exp && user.exp * 1000 < Date.now())) {
    clearToken();
    return null;
  }
  return user;
}

/** Captura el `#token=...` que deja el callback de Google y limpia la URL. */
export function captureTokenFromUrl(): boolean {
  const m = window.location.hash.match(/token=([^&]+)/);
  if (!m) return false;
  setToken(decodeURIComponent(m[1]));
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return true;
}

export function loginUrl(): string {
  const returnTo = encodeURIComponent(window.location.origin + window.location.pathname);
  return `/auth/google/login?return_to=${returnTo}`;
}

/** Cierra sesión: borra el token y vuelve a la pantalla de login (no re-dispara Google;
 *  el usuario elige cuándo volver a entrar con el botón "Continuar con Google"). */
export function logout(): void {
  clearToken();
  window.location.href = "/";
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

/** Parchea window.fetch una sola vez: inyecta `Authorization: Bearer` en toda llamada a
 *  /api y, si una respuesta /api da 401 teniendo token, cierra la sesión y vuelve al login. */
let _patched = false;
export function installFetchAuth(): void {
  if (_patched || typeof window === "undefined") return;
  _patched = true;
  const orig = window.fetch.bind(window);
  const wrapped: typeof window.fetch = async (input, init) => {
    const url = urlOf(input);
    const isApi =
      url.startsWith("/api") || url.startsWith(`${window.location.origin}/api`);
    const token = getToken();
    if (isApi && token) {
      const headers = new Headers(
        init?.headers || (input instanceof Request ? input.headers : undefined),
      );
      if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
      init = { ...init, headers };
    }
    const res = await orig(input, init);
    if (isApi && res.status === 401 && getToken()) {
      clearToken();
      window.location.href = "/";
    }
    return res;
  };
  window.fetch = wrapped;
}

installFetchAuth();
