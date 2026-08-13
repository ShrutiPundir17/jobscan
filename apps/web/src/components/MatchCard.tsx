import { useState } from "react";
import type { PersistedMatch } from "../types";
import { ScoreGauge } from "./ScoreGauge";

type Props = {
  match: PersistedMatch;
  busy?: boolean;
  onApply: () => void;
  onTailor: () => void;
  onDismiss?: () => void;
  onUndo?: () => void;
  dismissed?: boolean;
};

function companyInitials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

function postedLabel(iso: string | null): string {
  if (!iso) return "Recently";
  const ms = Date.now() - new Date(iso).getTime();
  const h = Math.floor(ms / 3600000);
  if (h < 1) return "Just now";
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function splitReasons(text: string | null): string[] {
  if (!text) return [];
  return text
    .split(/[.;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 3);
}

export function MatchCard({
  match,
  busy,
  onApply,
  onTailor,
  onDismiss,
  onUndo,
  dismissed,
}: Props) {
  const [details, setDetails] = useState(false);
  const reasons = splitReasons(match.match_reasoning);
  const gaps = match.skill_gaps.slice(0, 2);
  const verdict = (match.verdict || "fit").toLowerCase();
  const score = match.match_score ?? 0;
  const scoreBand =
    score >= 80 ? "score-high" : score >= 60 ? "score-mid" : "score-low";

  if (dismissed) {
    return (
      <article className="match-card dismissed fade-in">
        <div className="row space-between">
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            Dismissed · {match.job.title} @ {match.job.company}
          </span>
          {onUndo ? (
            <button type="button" className="btn btn-sm btn-ghost" onClick={onUndo}>
              Undo
            </button>
          ) : null}
        </div>
      </article>
    );
  }

  return (
    <article className={`match-card ${scoreBand}`}>
      {onDismiss ? (
        <button type="button" className="dismiss-btn" aria-label="Dismiss" onClick={onDismiss}>
          ×
        </button>
      ) : null}

      <div className="match-card-top">
        <div className="company-mark">{companyInitials(match.job.company)}</div>
        <div className="match-meta">
          <div className="match-title">{match.job.title}</div>
          <div className="match-sub">
            {match.job.company} · {match.job.location?.trim() || "Remote/unlisted"} ·{" "}
            {postedLabel(match.job.posted_at)}
          </div>
          <div className="match-sub" style={{ marginTop: 2 }}>
            via {match.job.source}
          </div>
        </div>
      </div>

      <div className="match-body">
        <div>
          <ScoreGauge score={match.match_score} />
          <div style={{ marginTop: 6, textAlign: "center" }}>
            <span className={`verdict-pill ${verdict}`}>{match.verdict ?? "Fit"}</span>
          </div>
        </div>
        <div className="stack" style={{ gap: "0.65rem" }}>
          <div>
            <div className="block-label">Why it matches</div>
            {reasons.length ? (
              <ul className="why-list">
                {reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <p className="muted" style={{ fontSize: "0.8rem", marginTop: 4 }}>
                Strong alignment with your profile.
              </p>
            )}
          </div>
          {gaps.length ? (
            <div>
              <div className="block-label">Gaps</div>
              <ul className="gap-list">
                {gaps.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {match.tailored_pitch ? <p className="pitch">{match.tailored_pitch}</p> : null}
        </div>
      </div>

      <div className="match-actions">
        <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={onApply}>
          Apply Now
        </button>
        <a className="btn btn-secondary btn-sm" href={match.job.url} target="_blank" rel="noreferrer">
          View Full JD
        </a>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={onTailor}>
          {busy ? "Working…" : match.tailored_resume_text ? "Re-tailor" : "Tailor"}
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setDetails((v) => !v)}
        >
          {details ? "Hide" : "Details"}
        </button>
      </div>

      {details && match.tailored_resume_text ? (
        <pre
          className="fade-in"
          style={{
            margin: 0,
            fontSize: "0.72rem",
            whiteSpace: "pre-wrap",
            maxHeight: 200,
            overflow: "auto",
            background: "var(--bg)",
            padding: "0.75rem",
            borderRadius: 8,
            border: "1px solid var(--border)",
          }}
        >
          {match.tailored_resume_text}
        </pre>
      ) : null}
    </article>
  );
}

export function MatchCardLoading() {
  return (
    <article className="match-card loading">
      <div className="row" style={{ gap: 12 }}>
        <div className="skeleton" style={{ width: 40, height: 40, borderRadius: 8 }} />
        <div style={{ flex: 1 }}>
          <div className="skeleton" style={{ height: 14, width: "70%", marginBottom: 8 }} />
          <div className="skeleton" style={{ height: 12, width: "45%" }} />
        </div>
      </div>
      <div style={{ textAlign: "center", padding: "1.5rem 0" }}>
        <div
          className="skeleton"
          style={{ width: 72, height: 72, borderRadius: "50%", margin: "0 auto 0.75rem" }}
        />
        <p className="muted">Analyzing match…</p>
      </div>
      <div className="skeleton" style={{ height: 10, marginBottom: 8 }} />
      <div className="skeleton" style={{ height: 10, width: "80%", marginBottom: 8 }} />
      <div className="skeleton" style={{ height: 10, width: "60%" }} />
    </article>
  );
}
