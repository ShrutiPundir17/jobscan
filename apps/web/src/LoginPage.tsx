import { FormEvent, useState } from "react";
import { api, setToken } from "./api";

type Props = {
  onLoggedIn: () => void;
  onBack?: () => void;
  initialMode?: Mode;
};

type Mode = "login" | "signup";

export function LoginPage({ onLoggedIn, onBack, initialMode = "login" }: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
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

  return (
    <div className="auth-page">
      <div className="auth-ambient" aria-hidden />
      <div className="auth-card fade-in">
        <div className="auth-logo">
          <p className="brand">
            Job<span>Agent</span>
          </p>
        </div>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            className={`auth-tab ${mode === "login" ? "active" : ""}`}
            onClick={() => {
              setMode("login");
              setError(null);
              setFieldErrors({});
            }}
          >
            Login
          </button>
          <button
            type="button"
            role="tab"
            className={`auth-tab ${mode === "signup" ? "active" : ""}`}
            onClick={() => {
              setMode("signup");
              setError(null);
              setFieldErrors({});
            }}
          >
            Sign Up
          </button>
        </div>

        <form className="stack" onSubmit={handleSubmit}>
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

          {error ? <div className="flash error">{error}</div> : null}

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
            onClick={() => setError("Google sign-in is coming soon.")}
          >
            <span className="g-dot" aria-hidden />
            Continue with Google
          </button>
        </form>

        {onBack ? (
          <button type="button" className="auth-back" onClick={onBack}>
            ← Back to home
          </button>
        ) : null}
      </div>
    </div>
  );
}
