import { useEffect, useState } from "react";
import { api } from "./api";
import type { ApplicationItem, PersistedMatch, UserProfile } from "./types";

type Props = {
  onNavigate: (tab: string) => void;
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

function useCountUp(target: number, ready: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!ready) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setN(target);
      return;
    }
    setN(0);
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 800);
      const eased = 1 - (1 - t) ** 3;
      setN(Math.round(target * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ready]);
  return n;
}

export function DashboardHome({ onNavigate, onMessage, onError }: Props) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [matches, setMatches] = useState<PersistedMatch[]>([]);
  const [apps, setApps] = useState<ApplicationItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        const [me, m, a] = await Promise.all([
          api.me(),
          api.listMatches(),
          api.listApplications(),
        ]);
        setProfile(me);
        setMatches(m.items);
        setApps(a.items);
      } catch (err) {
        onError(err instanceof Error ? err.message : "Failed to load dashboard");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const pending = apps.filter((a) =>
    ["pending_review", "applied", "interviewing"].includes(a.status),
  ).length;
  const interviews = apps.filter((a) => a.status === "interviewing").length;
  const offers = apps.filter((a) => a.status === "offered").length;
  const top = [...matches]
    .sort((a, b) => (b.match_score ?? 0) - (a.match_score ?? 0))
    .slice(0, 3);

  const cMatches = useCountUp(matches.length, !loading);
  const cPending = useCountUp(pending, !loading);
  const cInterviews = useCountUp(interviews, !loading);
  const cOffers = useCountUp(offers, !loading);

  if (loading) {
    return (
      <div className="fade-in">
        <div className="stats-row">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="stat-card">
              <div className="skeleton" style={{ height: 28, width: 48, marginBottom: 8 }} />
              <div className="skeleton" style={{ height: 12, width: 90 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const name = profile?.full_name?.split(/\s+/)[0] || "there";

  return (
    <div className="fade-in">
      <div className="row space-between" style={{ marginBottom: "0.35rem", flexWrap: "wrap" }}>
        <h1 style={{ fontSize: "1.5rem" }}>Welcome back, {name}</h1>
        {matches.length > 0 ? (
          <span className="live-ticker">{matches.length} new matches found</span>
        ) : null}
      </div>
      <p className="muted" style={{ marginBottom: "1.25rem" }}>
        Your agent is hunting. Here’s today’s snapshot.
      </p>

      <div className="stats-row">
        <button type="button" className="stat-card" onClick={() => onNavigate("matches")}>
          <div className="value" style={{ color: "var(--success)" }}>
            {cMatches}
          </div>
          <div className="label">New matches</div>
        </button>
        <button type="button" className="stat-card" onClick={() => onNavigate("applications")}>
          <div className="value" style={{ color: "var(--warning)" }}>
            {cPending}
          </div>
          <div className="label">Applications pending</div>
        </button>
        <button type="button" className="stat-card" onClick={() => onNavigate("applications")}>
          <div className="value">{cInterviews}</div>
          <div className="label">Interviews</div>
        </button>
        <button type="button" className="stat-card" onClick={() => onNavigate("applications")}>
          <div className="value" style={{ color: "var(--success)" }}>
            {cOffers}
          </div>
          <div className="label">Offers</div>
        </button>
      </div>

      <div className="section-head">
        <h2>Today&apos;s matches</h2>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onNavigate("matches")}>
          View all
        </button>
      </div>

      {top.length === 0 ? (
        <div className="empty-state" style={{ marginBottom: "1.5rem" }}>
          <div className="empty-illu">HUNTING</div>
          <p style={{ marginBottom: 8 }}>No matches yet — your agent is hunting.</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              onMessage(null);
              onNavigate("matches");
            }}
          >
            Scan & find matches
          </button>
        </div>
      ) : (
        <div className="today-grid">
          {top.map((m) => (
            <button
              type="button"
              className="stat-card"
              key={m.id}
              onClick={() => onNavigate("matches")}
              style={{ cursor: "pointer" }}
            >
              <div className="row space-between" style={{ marginBottom: 6 }}>
                <strong style={{ fontSize: "0.9rem" }}>{m.job.company}</strong>
                <span className="mono" style={{ color: "var(--success)" }}>
                  {m.match_score ?? "—"}
                </span>
              </div>
              <div className="label">{m.job.title}</div>
              <span className="method-pill">{m.verdict ?? "fit"}</span>
            </button>
          ))}
        </div>
      )}

      <div className="section-head">
        <h2>Recent activity</h2>
      </div>
      {apps.length === 0 ? (
        <div className="activity-empty">
          Activity will show here after you apply or update applications.
        </div>
      ) : (
        <div className="stack" style={{ gap: "0.45rem" }}>
          {apps.slice(0, 5).map((a) => (
            <div
              key={a.id}
              className="row space-between"
              style={{
                padding: "0.7rem 0.9rem",
                border: "1px solid var(--border)",
                borderRadius: 8,
                background: "var(--bg-elevated)",
              }}
            >
              <div>
                <div style={{ fontSize: "0.875rem", fontWeight: 600 }}>
                  {a.job.title} · {a.job.company}
                </div>
                <div className="muted" style={{ fontSize: "0.78rem" }}>
                  Status: {a.status}
                </div>
              </div>
              <span className="mono muted" style={{ fontSize: "0.8rem" }}>
                {a.match_score ?? "—"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
