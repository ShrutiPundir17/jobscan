import { FormEvent, useEffect, useState } from "react";
import { api, setToken } from "./api";
import { requestGoogleAccessToken } from "./googleAuth";

type Props = {
  onLoggedIn: () => void;
  onBack?: () => void;
  initialMode?: Mode;
  initialResetToken?: string | null;
};

type Mode = "login" | "signup" | "forgot" | "reset";
type ForgotKind = "password" | "username";

function readResetTokenFromUrl(): string | null {
  try {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("reset_token");
    return token && token.trim() ? token.trim() : null;
  } catch {
    return null;
  }
}

function clearResetTokenFromUrl() {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("reset_token")) return;
    url.searchParams.delete("reset_token");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  } catch {
    /* ignore */
  }
}

export function LoginPage({
  onLoggedIn,
  onBack,
  initialMode = "login",
  initialResetToken = null,
}: Props) {
  const urlToken = initialResetToken ?? readResetTokenFromUrl();
  const [mode, setMode] = useState<Mode>(urlToken ? "reset" : initialMode);
  const [forgotKind, setForgotKind] = useState<ForgotKind>("password");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [resetToken, setResetToken] = useState(urlToken ?? "");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [googleClientId, setGoogleClientId] = useState<string | null>(null);
  const [googleBusy, setGoogleBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const cfg = await api.googleConfig();
        if (!cancelled && cfg.enabled && cfg.client_id) {
          setGoogleClientId(cfg.client_id);
        }
      } catch {
        /* Google optional */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function goMode(next: Mode) {
    setMode(next);
    setError(null);
    setInfo(null);
    setFieldErrors({});
  }

  function assertSignupEmail(value: string) {
    const cleaned = value.trim().toLowerCase();
    const basic = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
    if (!basic.test(cleaned)) {
      throw new Error("Invalid email format");
    }
    const domain = cleaned.split("@")[1] ?? "";
    const blocked = new Set(["example.com", "example.org", "test.com", "localhost"]);
    if (blocked.has(domain) || domain.endsWith(".test") || domain.endsWith(".local")) {
      throw new Error("Please use a real email address.");
    }
    return cleaned;
  }

  async function handleAuthSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setFieldErrors({});
    setLoading(true);
    try {
      const cleanEmail =
        mode === "signup" ? assertSignupEmail(email) : email.trim().toLowerCase();
      if (mode === "signup") {
        const errs: Record<string, string> = {};
        if (!fullName.trim()) errs.name = "Name is required";
        if (password.length < 8) errs.password = "Password must be at least 8 characters";
        if (password !== confirm) errs.confirm = "Passwords do not match";
        if (Object.keys(errs).length) {
          setFieldErrors(errs);
          setLoading(false);
          return;
        }
        await api.register(cleanEmail, password, fullName.trim());
      }
      const res = await api.login(cleanEmail, password);
      setToken(res.access_token);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleForgotSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      if (forgotKind === "password") {
        const cleanEmail = email.trim().toLowerCase();
        const res = await api.forgotPassword(cleanEmail);
        if (res.reset_token) {
          setResetToken(res.reset_token);
          setInfo(res.message);
          goMode("reset");
          return;
        }
        setInfo(res.message);
      } else {
        const res = await api.forgotUsername(phone.trim());
        if (res.login_email) {
          setEmail(res.login_email);
          setInfo(`${res.message} Login email: ${res.login_email}`);
        } else {
          setInfo(res.message);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleResetSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setFieldErrors({});
    const errs: Record<string, string> = {};
    if (!resetToken.trim()) errs.token = "Reset token is required";
    if (password.length < 8) errs.password = "Password must be at least 8 characters";
    if (password !== confirm) errs.confirm = "Passwords do not match";
    if (Object.keys(errs).length) {
      setFieldErrors(errs);
      return;
    }
    setLoading(true);
    try {
      const res = await api.resetPassword(resetToken.trim(), password);
      clearResetTokenFromUrl();
      setInfo(res.message);
      setPassword("");
      setConfirm("");
      goMode("login");
      setInfo(res.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset password");
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogleSignIn() {
    setError(null);
    setInfo(null);
    if (!googleClientId) {
      setError(
        "Google Sign-In is not configured yet. Use email and password, or ask the admin to add GOOGLE_OAUTH_CLIENT_ID.",
      );
      return;
    }
    setGoogleBusy(true);
    try {
      const accessToken = await requestGoogleAccessToken(googleClientId);
      const res = await api.googleLogin({ access_token: accessToken });
      setToken(res.access_token);
      onLoggedIn();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Google Sign-In failed";
      if (/popup_closed|access_denied|cancelled/i.test(msg)) {
        setError("Google Sign-In was cancelled.");
      } else {
        setError(msg);
      }
    } finally {
      setGoogleBusy(false);
    }
  }

  const showTabs = mode === "login" || mode === "signup";

  return (
    <div className="auth-page">
      <div className="auth-ambient" aria-hidden />
      <div className="auth-card fade-in">
        <div className="auth-logo">
          <p className="brand">
            Job<span>Agent</span>
          </p>
        </div>

        {showTabs ? (
          <div className="auth-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              className={`auth-tab ${mode === "login" ? "active" : ""}`}
              onClick={() => goMode("login")}
            >
              Login
            </button>
            <button
              type="button"
              role="tab"
              className={`auth-tab ${mode === "signup" ? "active" : ""}`}
              onClick={() => goMode("signup")}
            >
              Sign Up
            </button>
          </div>
        ) : (
          <h2 className="auth-heading">
            {mode === "forgot"
              ? "Forgot username or password"
              : "Choose a new password"}
          </h2>
        )}

        {mode === "forgot" ? (
          <form className="stack" onSubmit={handleForgotSubmit}>
            <div className="auth-subtabs" role="tablist">
              <button
                type="button"
                className={`auth-subtab ${forgotKind === "password" ? "active" : ""}`}
                onClick={() => {
                  setForgotKind("password");
                  setError(null);
                  setInfo(null);
                }}
              >
                Reset password
              </button>
              <button
                type="button"
                className={`auth-subtab ${forgotKind === "username" ? "active" : ""}`}
                onClick={() => {
                  setForgotKind("username");
                  setError(null);
                  setInfo(null);
                }}
              >
                Recover email
              </button>
            </div>

            <p className="auth-help">
              {forgotKind === "password"
                ? "Enter the email you use to sign in. We’ll email a reset link when server email is configured; otherwise it goes to WhatsApp."
                : "Enter the phone number saved in your preferences. We’ll send your login email on WhatsApp (or show it here)."}
            </p>

            {forgotKind === "password" ? (
              <label className="field">
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@gmail.com"
                  required
                  autoComplete="email"
                />
              </label>
            ) : (
              <label className="field">
                Phone number
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+91 98765 43210"
                  required
                  autoComplete="tel"
                />
              </label>
            )}

            {error ? <div className="flash error">{error}</div> : null}
            {info ? <div className="flash success">{info}</div> : null}

            <button className="btn btn-primary btn-block" type="submit" disabled={loading}>
              {loading
                ? "Sending…"
                : forgotKind === "password"
                  ? "Send reset link"
                  : "Recover login email"}
            </button>

            <button type="button" className="auth-back" onClick={() => goMode("login")}>
              ← Back to login
            </button>
          </form>
        ) : null}

        {mode === "reset" ? (
          <form className="stack" onSubmit={handleResetSubmit}>
            <p className="auth-help">
              Enter a new password for your account. The reset link expires in one hour.
            </p>

            {!urlToken ? (
              <label className={`field ${fieldErrors.token ? "has-error" : ""}`}>
                Reset token
                <input
                  type="text"
                  value={resetToken}
                  onChange={(e) => setResetToken(e.target.value)}
                  placeholder="Paste token from email"
                  required
                  autoComplete="off"
                />
                {fieldErrors.token ? (
                  <span className="field-error">{fieldErrors.token}</span>
                ) : null}
              </label>
            ) : null}

            <label className={`field ${fieldErrors.password ? "has-error" : ""}`}>
              New password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 8 characters"
                required
                minLength={8}
                autoComplete="new-password"
              />
              {fieldErrors.password ? (
                <span className="field-error">{fieldErrors.password}</span>
              ) : null}
            </label>

            <label className={`field ${fieldErrors.confirm ? "has-error" : ""}`}>
              Confirm new password
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="Repeat password"
                required
                autoComplete="new-password"
              />
              {fieldErrors.confirm ? (
                <span className="field-error">{fieldErrors.confirm}</span>
              ) : null}
            </label>

            {error ? <div className="flash error">{error}</div> : null}
            {info ? <div className="flash success">{info}</div> : null}

            <button className="btn btn-primary btn-block" type="submit" disabled={loading}>
              {loading ? "Updating…" : "Update password"}
            </button>

            <button type="button" className="auth-back" onClick={() => goMode("login")}>
              ← Back to login
            </button>
          </form>
        ) : null}

        {showTabs ? (
          <form className="stack" onSubmit={handleAuthSubmit}>
            {mode === "signup" ? (
              <label className={`field ${fieldErrors.name ? "has-error" : ""}`}>
                Name
                <input
                  type="text"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Your full name"
                  autoComplete="name"
                />
                {fieldErrors.name ? <span className="field-error">{fieldErrors.name}</span> : null}
              </label>
            ) : null}

            <label className="field">
              Email
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@gmail.com"
                required
                autoComplete="email"
              />
            </label>

            <label className={`field ${fieldErrors.password ? "has-error" : ""}`}>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "signup" ? "At least 8 characters" : "Your password"}
                required
                minLength={mode === "signup" ? 8 : 1}
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
              />
              {fieldErrors.password ? (
                <span className="field-error">{fieldErrors.password}</span>
              ) : null}
            </label>

            {mode === "signup" ? (
              <label className={`field ${fieldErrors.confirm ? "has-error" : ""}`}>
                Confirm password
                <input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="Repeat password"
                  autoComplete="new-password"
                />
                {fieldErrors.confirm ? (
                  <span className="field-error">{fieldErrors.confirm}</span>
                ) : null}
              </label>
            ) : null}

            {mode === "login" ? (
              <button
                type="button"
                className="auth-forgot-link"
                onClick={() => goMode("forgot")}
              >
                Forgot username or password?
              </button>
            ) : null}

            {error ? <div className="flash error">{error}</div> : null}
            {info ? <div className="flash success">{info}</div> : null}

            <button className="btn btn-primary btn-block" type="submit" disabled={loading}>
              {loading
                ? mode === "signup"
                  ? "Creating…"
                  : "Signing in…"
                : mode === "signup"
                  ? "Create Account"
                  : "Sign In"}
            </button>

            <div className="auth-divider">or</div>

            <button
              type="button"
              className="btn btn-google btn-block"
              disabled={loading || googleBusy}
              onClick={() => void handleGoogleSignIn()}
            >
              <span className="g-dot" aria-hidden />
              {googleBusy ? "Connecting to Google…" : "Continue with Google"}
            </button>
          </form>
        ) : null}

        {onBack && showTabs ? (
          <button type="button" className="auth-back" onClick={onBack}>
            ← Back to home
          </button>
        ) : null}
      </div>
    </div>
  );
}
