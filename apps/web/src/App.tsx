import { useEffect, useState } from "react";
import { api, clearToken, getToken, SIGNED_OUT_EVENT } from "./api";
import { ApplicationsDashboard } from "./ApplicationsDashboard";
import { DashboardHome } from "./DashboardHome";
import { LandingPage } from "./LandingPage";
import { LoginPage } from "./LoginPage";
import { MatchesDashboard } from "./MatchesDashboard";
import { NotificationsDrawer } from "./NotificationsDrawer";
import { JobsPage } from "./JobsPage";
import { isOnboarded, OnboardingFlow } from "./OnboardingFlow";
import { PreferencesPage } from "./PreferencesPage";
import { ResumePage } from "./ResumePage";
import type { UserProfile } from "./types";

type Gate = "landing" | "login" | "signup" | "boot" | "onboarding" | "app";
type Tab =
  | "dashboard"
  | "matches"
  | "jobs"
  | "applications"
  | "resume"
  | "preferences";

// `short` fits six tabs in the mobile bar.
const NAV: { id: Tab; label: string; short: string; icon: string }[] = [
  { id: "dashboard", label: "Dashboard", short: "Home", icon: "▣" },
  { id: "matches", label: "Matches", short: "Matches", icon: "◎" },
  { id: "jobs", label: "Jobs", short: "Jobs", icon: "⌕" },
  { id: "applications", label: "Applications", short: "Apps", icon: "▤" },
  { id: "resume", label: "Resume", short: "Resume", icon: "☰" },
  { id: "preferences", label: "Preferences", short: "Prefs", icon: "⚙" },
];

function readResetToken(): string | null {
  try {
    const token = new URLSearchParams(window.location.search).get("reset_token");
    return token && token.trim() ? token.trim() : null;
  } catch {
    return null;
  }
}

export default function App() {
  const resetToken = readResetToken();
  const [gate, setGate] = useState<Gate>(() => {
    if (resetToken) return "login";
    return getToken() ? "boot" : "landing";
  });
  const [tab, setTab] = useState<Tab>("dashboard");
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notifOpen, setNotifOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [jobsQuery, setJobsQuery] = useState("");
  const [jobsQuerySeq, setJobsQuerySeq] = useState(0);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    if (gate !== "boot") return;
    void (async () => {
      try {
        const me = await api.me();
        setProfile(me);
        if (isOnboarded()) {
          setGate("app");
          return;
        }
        const resumes = await api.listResumes();
        const ready =
          resumes.items.some((r) => r.parsed_data) && (me.target_roles?.length ?? 0) > 0;
        if (ready) {
          localStorage.setItem("jobagent_onboarded", "1");
          setGate("app");
        } else {
          setGate("onboarding");
        }
      } catch {
        clearToken();
        setGate("landing");
      }
    })();
  }, [gate]);

  // Any request that comes back 401 (expired or revoked session) signs the user out.
  useEffect(() => {
    const onSignedOut = () => {
      setProfile(null);
      setGate((g) => (g === "app" || g === "boot" || g === "onboarding" ? "login" : g));
      setTab("dashboard");
    };
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, []);

  function logout() {
    clearToken();
    setProfile(null);
    setGate("landing");
    setTab("dashboard");
  }

  function afterLogin() {
    setGate("boot");
  }

  if (gate === "landing") {
    return (
      <LandingPage
        onLogin={() => setGate("login")}
        onSignup={() => setGate("signup")}
      />
    );
  }

  if (gate === "login" || gate === "signup") {
    return (
      <LoginPage
        initialMode={gate === "signup" ? "signup" : "login"}
        initialResetToken={resetToken}
        onLoggedIn={afterLogin}
        onBack={() => setGate("landing")}
      />
    );
  }

  if (gate === "boot") {
    return (
      <div className="auth-page">
        <p className="muted">Preparing your workspace…</p>
      </div>
    );
  }

  if (gate === "onboarding") {
    return (
      <OnboardingFlow
        onDone={() => {
          setGate("app");
          setTab("dashboard");
          void api.me().then(setProfile).catch(() => undefined);
        }}
      />
    );
  }

  const initials = (profile?.full_name || profile?.email || "U")
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <p className="brand" style={{ padding: "0.35rem 0.75rem" }}>
          Job<span>Agent</span>
        </p>
        <nav className="sidebar-nav" aria-label="Main">
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`nav-item ${tab === item.id ? "active" : ""}`}
              onClick={() => {
                setTab(item.id);
                setMessage(null);
                setError(null);
              }}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </button>
          ))}
          <button type="button" className="nav-item" onClick={() => setNotifOpen(true)}>
            <span className="nav-icon">◉</span>
            Notifications
            {unread > 0 ? (
              <span className="mono" style={{ marginLeft: "auto", color: "var(--warning)" }}>
                {unread}
              </span>
            ) : null}
          </button>
        </nav>
        <div className="sidebar-user">
          <div className="avatar">{initials}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div
              style={{
                fontSize: "0.8rem",
                fontWeight: 600,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {profile?.full_name || "Account"}
            </div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={logout}>
              Log out
            </button>
          </div>
        </div>
      </aside>

      <div className="main-col">
        <header className="topbar">
          {unread > 0 ? (
            <span className="live-ticker">{unread} new updates</span>
          ) : null}
          <form
            className="topbar-search"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              setJobsQuery(searchText.trim());
              setJobsQuerySeq((n) => n + 1);
              setTab("jobs");
              setMessage(null);
              setError(null);
            }}
          >
            <input
              type="search"
              placeholder="Search jobs, companies…"
              aria-label="Search jobs, companies"
              maxLength={100}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
          </form>
          <div className="topbar-actions">
            <div className="bell-wrap">
              <button
                type="button"
                className="btn btn-icon btn-ghost"
                aria-label="Notifications"
                onClick={() => setNotifOpen(true)}
              >
                ◉
              </button>
              {unread > 0 ? <span className="bell-dot" /> : null}
            </div>
            <div className="avatar">{initials}</div>
          </div>
        </header>

        {error ? (
          <div className="flash error fade-in" style={{ margin: "0.75rem 1.5rem 0" }}>
            {error}
          </div>
        ) : null}
        {message ? (
          <div className="flash success fade-in" style={{ margin: "0.75rem 1.5rem 0" }}>
            {message}
          </div>
        ) : null}

        <main className="page">
          {tab === "dashboard" ? (
            <DashboardHome
              onNavigate={(t) => setTab(t as Tab)}
              onMessage={setMessage}
              onError={setError}
            />
          ) : null}
          {tab === "matches" ? (
            <MatchesDashboard onMessage={setMessage} onError={setError} />
          ) : null}
          {tab === "jobs" ? (
            <JobsPage query={jobsQuery} querySeq={jobsQuerySeq} onMessage={setMessage} onError={setError} />
          ) : null}
          {tab === "applications" ? (
            <ApplicationsDashboard onMessage={setMessage} onError={setError} />
          ) : null}
          {tab === "resume" ? (
            <ResumePage onMessage={setMessage} onError={setError} />
          ) : null}
          {tab === "preferences" ? (
            <PreferencesPage onMessage={setMessage} onError={setError} />
          ) : null}
        </main>
      </div>

      <nav className="mobile-tabs" aria-label="Mobile">
        {NAV.map((item) => (
          <button
            key={item.id}
            type="button"
            className={tab === item.id ? "active" : ""}
            onClick={() => setTab(item.id)}
          >
            <span>{item.icon}</span>
            {item.short}
          </button>
        ))}
      </nav>

      <NotificationsDrawer
        open={notifOpen}
        onClose={() => setNotifOpen(false)}
        onChanged={setUnread}
      />
    </div>
  );
}
