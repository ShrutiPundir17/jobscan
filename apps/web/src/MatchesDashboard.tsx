import { useEffect, useState } from "react";
import { api } from "./api";
import type { PersistedMatch } from "./types";

type Props = {
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

export function MatchesDashboard({ onMessage, onError }: Props) {
  const [items, setItems] = useState<PersistedMatch[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [scoring, setScoring] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    onError(null);
    try {
      const res = await api.listMatches();
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to load matches");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function findMatches() {
    setScoring(true);
    onMessage(null);
    onError(null);
    onMessage("Scoring matches with Gemini — this can take 1–3 minutes. Keep this tab open…");
    try {
      const res = await api.scoreMatches({
        limit: 10,
        persist: true,
        apply_location_prefs: true,
      });
      onMessage(
        `Scored ${res.count} roles — saved ${res.persisted_count} matches (min ${res.min_match_score}).`,
      );
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Match scoring failed";
      onError(
        /abort|timeout|failed to fetch|network/i.test(msg)
          ? "Find matches timed out or lost connection. Gemini may be busy — wait a minute and try again."
          : msg,
      );
      onMessage(null);
    } finally {
      setScoring(false);
    }
  }

  async function scanJobs() {
    setScanning(true);
    onMessage(null);
    onError(null);
    try {
      const scan = await api.triggerScan();
      await api.triggerEmbedJobs();
      onMessage(`${scan.message} Embeddings queued. Wait ~1–2 min, then Find matches.`);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  }

  async function tailor(id: string) {
    setBusyId(id);
    onMessage(null);
    onError(null);
    try {
      const res = await api.tailorMatch(id);
      setItems((prev) =>
        prev.map((m) =>
          m.id === id
            ? {
                ...m,
                tailored_bullets: res.tailored_bullets,
                tailored_resume_text: res.tailored_resume_text,
                tailored_pitch: res.tailored_pitch,
              }
            : m,
        ),
      );
      setExpandedId(id);
      onMessage(`Tailored resume ready for ${res.job.title} @ ${res.job.company}.`);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Tailor failed");
    } finally {
      setBusyId(null);
    }
  }

  async function apply(id: string, url: string) {
    setBusyId(id);
    onError(null);
    try {
      const res = await api.applyToJob(id);
      window.open(url, "_blank", "noopener,noreferrer");
      onMessage(res.message);
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Apply failed");
    } finally {
      setBusyId(null);
    }
  }

  function copyText(text: string) {
    void navigator.clipboard.writeText(text);
    onMessage("Copied tailored resume to clipboard.");
  }

  if (loading) {
    return <p className="muted">Loading matches…</p>;
  }

  return (
    <section className="panel panel-pad fade-in">
      <div className="row space-between" style={{ marginBottom: "1rem", gap: "0.75rem" }}>
        <div>
          <h2 className="section-title" style={{ marginBottom: 0 }}>
            Matches
          </h2>
          <p className="section-copy" style={{ marginTop: "0.35rem" }}>
            Scores, gaps, and JD-tailored bullets — {total} saved for you.
          </p>
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-soft"
            disabled={scanning || scoring}
            onClick={() => void scanJobs()}
          >
            {scanning ? "Scanning…" : "Scan jobs"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={scoring || scanning}
            onClick={() => void findMatches()}
          >
            {scoring ? "Scoring…" : "Find matches"}
          </button>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="muted" style={{ display: "grid", gap: "0.55rem" }}>
          <p style={{ margin: 0 }}>
            No matches for your current preferred locations yet.
          </p>
          <p style={{ margin: 0 }}>
            1) On <strong>Profile</strong>, add <strong>Target roles</strong> (e.g. Software
            Engineer) and save.
            <br />
            2) Click <strong>Scan jobs</strong> here (uses your cities) and wait ~1–2 minutes.
            <br />
            3) Click <strong>Find matches</strong> again.
          </p>
        </div>
      ) : (
        <div className="match-list">
          {items.map((m) => {
            const open = expandedId === m.id;
            return (
              <article className="match-card" key={m.id}>
                <div className="row space-between" style={{ gap: "0.75rem", alignItems: "flex-start" }}>
                  <div>
                    <div className="match-title">{m.job.title}</div>
                    <div className="muted">
                      {m.job.company} · {m.job.source}
                    </div>
                    <div className={`match-location ${m.job.location ? "" : "missing"}`}>
                      {m.job.location?.trim() || "Location not listed"}
                    </div>
                  </div>
                  <div className="match-score-block">
                    <span className="match-score">{m.match_score ?? "—"}</span>
                    <span className={`status-pill ${m.verdict === "strong" || m.verdict === "good" ? "ok" : "warn"}`}>
                      {m.verdict ?? m.status}
                    </span>
                  </div>
                </div>

                {m.match_reasoning ? (
                  <p className="match-reason">{m.match_reasoning}</p>
                ) : null}

                {m.skill_gaps.length > 0 ? (
                  <div className="chip-row" style={{ marginTop: "0.5rem" }}>
                    {m.skill_gaps.slice(0, 6).map((g) => (
                      <span className="chip" key={g}>
                        gap: {g}
                      </span>
                    ))}
                  </div>
                ) : null}

                <div className="row" style={{ marginTop: "0.85rem", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn btn-soft"
                    disabled={busyId === m.id}
                    onClick={() => void tailor(m.id)}
                  >
                    {busyId === m.id ? "Tailoring…" : m.tailored_resume_text ? "Re-tailor" : "Tailor resume"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-accent"
                    disabled={busyId === m.id}
                    onClick={() => void apply(m.id, m.job.url)}
                  >
                    Apply
                  </button>
                  <a className="btn btn-ghost" href={m.job.url} target="_blank" rel="noreferrer">
                    Open JD
                  </a>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setExpandedId(open ? null : m.id)}
                  >
                    {open ? "Hide details" : "Details"}
                  </button>
                </div>

                {open ? (
                  <div className="match-detail fade-in">
                    {m.tailored_pitch ? (
                      <>
                        <h4>Pitch</h4>
                        <p>{m.tailored_pitch}</p>
                      </>
                    ) : null}
                    {m.tailored_resume_text ? (
                      <>
                        <div className="row space-between">
                          <h4>Tailored resume</h4>
                          <button
                            type="button"
                            className="btn btn-soft"
                            onClick={() => copyText(m.tailored_resume_text || "")}
                          >
                            Copy
                          </button>
                        </div>
                        <pre className="tailored-pre">{m.tailored_resume_text}</pre>
                      </>
                    ) : (
                      <p className="muted">No tailored resume yet — click Tailor resume.</p>
                    )}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
