import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { MatchCard, MatchCardLoading } from "./components/MatchCard";
import type { PersistedMatch } from "./types";

type Props = {
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

const FILTERS = ["All", "Strong", "Good", "Weak", "LinkedIn", "Internshala", "Unstop"] as const;

export function MatchesDashboard({ onMessage, onError }: Props) {
  const [items, setItems] = useState<PersistedMatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [scoring, setScoring] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("All");

  async function load() {
    setLoading(true);
    onError(null);
    try {
      const res = await api.listMatches();
      setItems(res.items);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to load matches");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const filtered = useMemo(() => {
    return items.filter((m) => {
      if (dismissed.has(m.id)) return true;
      const v = (m.verdict || "").toLowerCase();
      const src = (m.job.source || "").toLowerCase();
      if (filter === "All") return true;
      if (filter === "Strong") return v === "strong";
      if (filter === "Good") return v === "good" || v === "strong";
      if (filter === "Weak") return v === "weak" || v === "poor";
      return src.includes(filter.toLowerCase());
    });
  }, [items, filter, dismissed]);

  const visible = filtered.filter((m) => !dismissed.has(m.id));

  async function findMatches() {
    setScoring(true);
    onMessage("Scoring matches — keep this tab open…");
    onError(null);
    try {
      const res = await api.scoreMatches({
        limit: 10,
        persist: true,
        apply_location_prefs: true,
      });
      onMessage(`Scored ${res.count} · saved ${res.persisted_count} matches.`);
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Match scoring failed";
      onError(
        /abort|timeout|failed to fetch|network/i.test(msg)
          ? "Find matches timed out. Wait a minute and try again."
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
      onMessage(`${scan.message} Wait ~1–2 min, then Find matches.`);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  }

  async function tailor(id: string): Promise<boolean> {
    setBusyId(id);
    onError(null);
    onMessage("Tailoring resume for this job — usually 15–40s…");
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
      onMessage(`Tailored for ${res.job.title} @ ${res.job.company}.`);
      return true;
    } catch (err) {
      onError(err instanceof Error ? err.message : "Tailor failed");
      onMessage(null);
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function apply(id: string, url: string) {
    setBusyId(id);
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

  return (
    <div className="fade-in">
      <div className="row space-between" style={{ marginBottom: "1rem", flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: "1.35rem" }}>Matches</h1>
          <p className="muted" style={{ fontSize: "0.875rem" }}>
            {visible.length} ranked for you
          </p>
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-secondary"
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

      <div className="filter-bar">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className={`filter-chip ${filter === f ? "active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>

      {scoring ? (
        <div className="match-grid" style={{ marginBottom: "1rem" }}>
          <MatchCardLoading />
          <MatchCardLoading />
        </div>
      ) : null}

      {loading ? (
        <p className="muted">Loading matches…</p>
      ) : visible.length === 0 ? (
        <div className="empty-state">
          <div className="empty-illu">EMPTY STATE</div>
          <p className="block-label" style={{ marginBottom: 8 }}>
            0 results
          </p>
          <p className="muted" style={{ marginBottom: 12 }}>
            {items.length === 0
              ? "No matches yet — your agent is hunting. Scan jobs, then Find matches."
              : "Try adjusting your filters or preferences."}
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              if (items.length === 0) void findMatches();
              else setFilter("All");
            }}
          >
            {items.length === 0 ? "Find matches" : "Clear filters"}
          </button>
        </div>
      ) : (
        <div className="match-grid">
          {filtered.map((m) => (
            <MatchCard
              key={m.id}
              match={m}
              busy={busyId === m.id}
              dismissed={dismissed.has(m.id)}
              onDismiss={() => setDismissed((s) => new Set(s).add(m.id))}
              onUndo={() =>
                setDismissed((s) => {
                  const n = new Set(s);
                  n.delete(m.id);
                  return n;
                })
              }
              onApply={() => void apply(m.id, m.job.url)}
              onTailor={() => tailor(m.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
