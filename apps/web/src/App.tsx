import { useState } from "react";
import { clearToken, getToken } from "./api";
import { ApplicationsDashboard } from "./ApplicationsDashboard";
import { LoginPage } from "./LoginPage";
import { MatchesDashboard } from "./MatchesDashboard";
import { ProfileDashboard } from "./ProfileDashboard";

type Tab = "profile" | "matches" | "applications";

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  const [tab, setTab] = useState<Tab>("profile");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!authed) {
    return <LoginPage onLoggedIn={() => setAuthed(true)} />;
  }

  return (
    <div className="app-shell">
      <nav className="app-nav" aria-label="Main">
        <p className="brand brand-nav">
          Job<span>Agent</span>
        </p>
        <div className="app-nav-tabs">
          {(
            [
              ["profile", "Profile"],
              ["matches", "Matches"],
              ["applications", "Applications"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`nav-tab ${tab === id ? "active" : ""}`}
              onClick={() => {
                setTab(id);
                setMessage(null);
                setError(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => {
            clearToken();
            setAuthed(false);
          }}
        >
          Log out
        </button>
      </nav>

      {tab !== "profile" && error ? <div className="flash error fade-in">{error}</div> : null}
      {tab !== "profile" && message ? <div className="flash success fade-in">{message}</div> : null}

      {tab === "profile" ? (
        <ProfileDashboard onLogout={() => setAuthed(false)} hideChrome />
      ) : null}
      {tab === "matches" ? (
        <MatchesDashboard onMessage={setMessage} onError={setError} />
      ) : null}
      {tab === "applications" ? (
        <ApplicationsDashboard onMessage={setMessage} onError={setError} />
      ) : null}
    </div>
  );
}
