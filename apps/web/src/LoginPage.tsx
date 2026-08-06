import { FormEvent, useState } from "react";
import { api, setToken } from "./api";

type Props = {
  onLoggedIn: () => void;
};

type Mode = "login" | "signup";

export function LoginPage({ onLoggedIn }: Props) {
  const [mode, setMode] = useState<Mode>("login");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function assertSignupEmail(value: string) {
    const cleaned = value.trim().toLowerCase();
    const basic = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
    if (!basic.test(cleaned)) {
      throw new Error("Please enter a valid email address (e.g. name@gmail.com).");
    }
    const domain = cleaned.split("@")[1] ?? "";
    const blocked = new Set([
      "example.com",
      "example.org",
      "example.net",
      "test.com",
      "test.org",
      "localhost",
      "invalid",
      "local",
    ]);
    if (blocked.has(domain) || domain.endsWith(".test") || domain.endsWith(".local")) {
      throw new Error("Please use a real email address from a mailbox you can access.");
    }
    return cleaned;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const cleanEmail =
        mode === "signup" ? assertSignupEmail(email) : email.trim().toLowerCase();
      if (mode === "signup") {
        if (password.length < 8) {
          throw new Error("Password must be at least 8 characters");
        }
        await api.register(cleanEmail, password, fullName.trim() || undefined);
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
    <div className="login-page">
      <div className="login-atmosphere" aria-hidden>
        <div className="login-scanline" />
        <div className="login-orb login-orb-a" />
        <div className="login-orb login-orb-b" />
      </div>

      <div className="login-stage">
        <header className="login-hero">
          <p className="brand brand-hero">
            Job<span>Agent</span>
          </p>
          <p className="login-tagline">Hunt while you sleep.</p>
        </header>

        <form className="login-form stack" onSubmit={handleSubmit}>
          <p className="lede">
            {mode === "login"
              ? "Sign in — resume in, ranked roles out."
              : "Create an account and start the match engine."}
          </p>

          <div className="auth-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              className={`auth-tab ${mode === "login" ? "active" : ""}`}
              onClick={() => {
                setMode("login");
                setError(null);
              }}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              className={`auth-tab ${mode === "signup" ? "active" : ""}`}
              onClick={() => {
                setMode("signup");
                setError(null);
              }}
            >
              Create account
            </button>
          </div>

          {mode === "signup" ? (
            <label className="field">
              Full name
              <input
                type="text"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Your name"
                autoComplete="name"
              />
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

          <label className="field">
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
          </label>

          {error ? <div className="flash error">{error}</div> : null}

          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading
              ? mode === "signup"
                ? "Creating account…"
                : "Signing in…"
              : mode === "signup"
                ? "Create account"
                : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
