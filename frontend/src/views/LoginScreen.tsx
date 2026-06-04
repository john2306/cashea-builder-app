import { loginUrl } from "../lib/auth";

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.76c-.98.66-2.23 1.06-3.72 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.11a6.6 6.6 0 0 1 0-4.22V7.05H2.18a11 11 0 0 0 0 9.9l3.66-2.84z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.05l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"
      />
    </svg>
  );
}

/** Pantalla de login del builder. Autenticación SOLO con Google (vía gateway SSO). */
export function LoginScreen() {
  return (
    <div className="login-screen">
      <div className="login-glow" aria-hidden="true" />
      <div className="login-card">
        <img className="login-logo" src="/logo192.png" alt="Cashea Hub App" />
        <h1 className="login-title">Cashea Hub App</h1>
        <p className="login-sub">
          Build and deploy real apps with natural language.
        </p>
        <a className="login-google" href={loginUrl()}>
          <GoogleIcon />
          <span>Continue with Google</span>
        </a>
        <p className="login-foot">Access only with your Google account.</p>
      </div>
    </div>
  );
}
