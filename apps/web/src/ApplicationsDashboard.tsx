import { useEffect, useState } from "react";
import { api } from "./api";
import type { ApplicationItem } from "./types";

type Props = {
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

const STATUSES = [
  "pending_review",
  "applied",
  "interviewing",
  "offered",
  "rejected",
  "withdrawn",
] as const;

export function ApplicationsDashboard({ onMessage, onError }: Props) {
  const [items, setItems] = useState<ApplicationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load(nextFilter = filter) {
    setLoading(true);
    onError(null);
    try {
      const res = await api.listApplications(nextFilter || undefined);
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to load applications");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function setStatus(id: string, status: string) {
    setBusyId(id);
    onError(null);
    try {
      await api.updateApplication(id, { status });
      onMessage(`Updated status to ${status}.`);
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusyId(null);
    }
  }

  async function withdraw(id: string) {
    setBusyId(id);
    onError(null);
    try {
      await api.withdrawApplication(id);
      onMessage("Application withdrawn.");
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Withdraw failed");
    } finally {
      setBusyId(null);
    }
  }

  async function saveNotes(id: string, notes: string) {
    setBusyId(id);
    try {
      await api.updateApplication(id, { notes: notes || null });
      onMessage("Notes saved.");
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not save notes");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="panel panel-pad fade-in">
      <div className="row space-between" style={{ marginBottom: "1rem" }}>
        <div>
          <h2 className="section-title" style={{ marginBottom: 0 }}>
            Applications
          </h2>
          <p className="section-copy" style={{ marginTop: "0.35rem" }}>
            Track every role you’ve reviewed or applied to — {total} total.
          </p>
        </div>
        <label className="field" style={{ minWidth: "12rem", margin: 0 }}>
          Filter status
          <select
            value={filter}
            onChange={(e) => {
              const v = e.target.value;
              setFilter(v);
              void load(v);
            }}
          >
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading ? (
        <p className="muted">Loading applications…</p>
      ) : items.length === 0 ? (
        <p className="muted">No applications yet. Find matches first, then Apply from Matches.</p>
      ) : (
        <div className="match-list">
          {items.map((a) => (
            <article className="match-card" key={a.id}>
              <div className="row space-between" style={{ gap: "0.75rem" }}>
                <div>
                  <div className="match-title">{a.job.title}</div>
                  <div className="muted">
                    {a.job.company}
                  </div>
                  <div className={`match-location ${a.job.location ? "" : "missing"}`}>
                    {a.job.location?.trim() || "Location not listed"}
                  </div>
                </div>
                <div className="match-score-block">
                  <span className="match-score">{a.match_score ?? "—"}</span>
                  <span className="status-pill ok">{a.status}</span>
                </div>
              </div>

              {a.applied_at ? (
                <p className="muted" style={{ marginBottom: 0 }}>
                  Applied {new Date(a.applied_at).toLocaleString()}
                </p>
              ) : null}

              <div className="row" style={{ marginTop: "0.75rem", flexWrap: "wrap" }}>
                <label className="field" style={{ margin: 0, minWidth: "10rem" }}>
                  Status
                  <select
                    value={a.status}
                    disabled={busyId === a.id}
                    onChange={(e) => void setStatus(a.id, e.target.value)}
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                    {!STATUSES.includes(a.status as (typeof STATUSES)[number]) ? (
                      <option value={a.status}>{a.status}</option>
                    ) : null}
                  </select>
                </label>
                <a className="btn btn-ghost" href={a.job.url} target="_blank" rel="noreferrer">
                  Open JD
                </a>
                <button
                  type="button"
                  className="btn btn-soft"
                  disabled={busyId === a.id}
                  onClick={() => void withdraw(a.id)}
                >
                  Withdraw
                </button>
              </div>

              <label className="field" style={{ marginTop: "0.75rem" }}>
                Notes
                <textarea
                  defaultValue={a.notes ?? ""}
                  rows={2}
                  placeholder="Interview date, recruiter name…"
                  onBlur={(e) => {
                    if ((e.target.value || "") !== (a.notes || "")) {
                      void saveNotes(a.id, e.target.value);
                    }
                  }}
                />
              </label>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
