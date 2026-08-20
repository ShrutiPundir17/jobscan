import { useState } from "react";
import type { PersistedMatch } from "../types";
import { ScoreGauge } from "./ScoreGauge";

type Props = {
  match: PersistedMatch;
  busy?: boolean;
  onApply: () => void;
  /** Return true when tailor succeeded so the card can open Details. */
  onTailor: () => void | Promise<boolean | void>;
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
  const [copied, setCopied] = useState(false);
  const reasons = splitReasons(match.match_reasoning);
  const gaps = match.skill_gaps.slice(0, 2);
  const allGaps = match.skill_gaps;
  const verdict = (match.verdict || "fit").toLowerCase();
  const score = match.match_score ?? 0;
  const scoreBand =
    score >= 80 ? "score-high" : score >= 60 ? "score-mid" : "score-low";
  const jd = (match.job.description || "").trim();

  async function handleTailor() {
    const ok = await onTailor();
    if (ok !== false) setDetails(true);
  }

  async function copyTailored() {
    const text = match.tailored_resume_text;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* ignore */
    }
  }

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
        <button
          type="button"
          className="btn btn-soft btn-sm"
          disabled={busy}
          onClick={() => void handleTailor()}
        >
          {busy ? "Tailoring…" : match.tailored_resume_text ? "Re-tailor" : "Tailor"}
        </button>
        <button
          type="button"
          className="btn btn-soft btn-sm"
          aria-expanded={details}
          onClick={() => setDetails((v) => !v)}
        >
          {details ? "Hide" : "Details"}
        </button>
      </div>

      {details ? (
        <div className="match-details fade-in">
          {match.match_reasoning ? (
            <section>
              <div className="block-label">Full match reasoning</div>
              <p className="match-details-text">{match.match_reasoning}</p>
            </section>
          ) : null}

          {allGaps.length ? (
            <section>
              <div className="block-label">All skill gaps</div>
              <ul className="gap-list">
                {allGaps.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            </section>
          ) : null}

          {jd ? (
            <section>
              <div className="block-label">Job description</div>
              <p className="match-details-text match-jd">{jd}</p>
            </section>
          ) : (
            <section>
              <div className="block-label">Job description</div>
              <p className="muted" style={{ fontSize: "0.8rem", marginTop: 4 }}>
                No JD text stored for this listing — use View Full JD on the employer site.
              </p>
            </section>
          )}

          <section>
            <div className="row space-between" style={{ alignItems: "center", marginBottom: 6 }}>
              <div className="block-label" style={{ margin: 0 }}>
                Tailored resume
              </div>
              {match.tailored_resume_text ? (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => void copyTailored()}>
                  {copied ? "Copied" : "Copy"}
                </button>
              ) : null}
            </div>
            {match.tailored_resume_text ? (
              <pre className="match-tailored-pre">{match.tailored_resume_text}</pre>
            ) : (
              <p className="muted" style={{ fontSize: "0.8rem", marginTop: 4 }}>
                No tailored version yet. Click <strong>Tailor</strong> to rewrite your resume for
                this role (takes ~15–40s).
              </p>
            )}
          </section>
        </div>
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
