import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { ApplicationItem } from "./types";

type Props = {
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: "applied", label: "Applied", statuses: ["applied", "pending_review"] },
  { key: "viewed", label: "Viewed", statuses: ["viewed"] },
  { key: "phone", label: "Phone Screen", statuses: ["phone_screen"] },
  { key: "interview", label: "Interview", statuses: ["interviewing"] },
  { key: "offer", label: "Offer", statuses: ["offered"] },
  { key: "rejected", label: "Rejected", statuses: ["rejected", "withdrawn"] },
];

export function ApplicationsDashboard({ onMessage, onError }: Props) {
  const [items, setItems] = useState<ApplicationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ApplicationItem | null>(null);
  const [notes, setNotes] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    onError(null);
    try {
      const res = await api.listApplications();
      setItems(res.items);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to load applications");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const byCol = useMemo(() => {
    const map: Record<string, ApplicationItem[]> = {};
    for (const col of COLUMNS) map[col.key] = [];
    for (const item of items) {
      const col = COLUMNS.find((c) => c.statuses.includes(item.status));
      if (col) map[col.key].push(item);
      else map.applied.push(item);
    }
    return map;
  }, [items]);

  async function moveTo(id: string, status: string) {
    setBusyId(id);
    try {
      await api.updateApplication(id, { status });
      onMessage(`Moved to ${status}.`);
      await load();
      if (selected?.id === id) {
        const updated = (await api.listApplications()).items.find((x) => x.id === id);
        setSelected(updated ?? null);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusyId(null);
    }
  }

  async function saveNotes() {
    if (!selected) return;
    setBusyId(selected.id);
    try {
      await api.updateApplication(selected.id, { notes: notes || null });
      onMessage("Notes saved.");
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not save notes");
    } finally {
      setBusyId(null);
    }
  }

  function methodLabel(a: ApplicationItem): string {
    if (a.applied_at) return "One-tap";
    return "Manual";
  }

  if (loading) return <p className="muted">Loading applications…</p>;

  return (
    <div className="fade-in page-wide" style={{ maxWidth: "100%" }}>
      <div className="section-head">
        <div>
          <h1 style={{ fontSize: "1.35rem" }}>Applications</h1>
          <p className="muted" style={{ fontSize: "0.875rem" }}>
            {items.length} in your pipeline
          </p>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="empty-state">
          <div className="empty-illu">TRACKER</div>
          <p className="muted">No applications yet. Find matches first, then Apply.</p>
        </div>
      ) : (
        <div className="kanban">
          {COLUMNS.map((col) => (
            <div className="kanban-col" key={col.key}>
              <h3>
                {col.label}
                <span className="mono">{byCol[col.key].length}</span>
              </h3>
              {byCol[col.key].length === 0 ? (
                <p className="dim" style={{ fontSize: "0.75rem", padding: "0.5rem 0" }}>
                  Drop applications here
                </p>
              ) : null}
              {byCol[col.key].map((a) => (
                <button
                  type="button"
                  className="kanban-card"
                  key={a.id}
                  onClick={() => {
                    setSelected(a);
                    setNotes(a.notes ?? "");
                  }}
                >
                  <strong style={{ fontSize: "0.85rem" }}>{a.job.company}</strong>
                  <div className="muted" style={{ fontSize: "0.78rem" }}>
                    {a.job.title}
                  </div>
                  <div className="muted" style={{ fontSize: "0.72rem", marginTop: 4 }}>
                    {a.applied_at
                      ? new Date(a.applied_at).toLocaleDateString()
                      : new Date(a.created_at).toLocaleDateString()}
                  </div>
                  <span className="method-pill">{methodLabel(a)}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      {selected ? (
        <>
          <div className="drawer-scrim" onClick={() => setSelected(null)} />
          <aside className="drawer" aria-label="Application detail">
            <div className="drawer-head">
              <strong>{selected.job.company}</strong>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
            <div className="drawer-body stack">
              <div>
                <div style={{ fontWeight: 600 }}>{selected.job.title}</div>
                <div className="muted" style={{ fontSize: "0.8rem" }}>
                  Score {selected.match_score ?? "—"} · {selected.status}
                </div>
              </div>
              <div className="block-label">Move to</div>
              <div className="chip-row">
                {COLUMNS.map((c) => (
                  <button
                    key={c.key}
                    type="button"
                    className="btn btn-sm btn-secondary"
                    disabled={busyId === selected.id}
                    onClick={() => void moveTo(selected.id, c.statuses[0])}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
              <label className="field">
                Notes
                <textarea
                  rows={4}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busyId === selected.id}
                onClick={() => void saveNotes()}
              >
                Save notes
              </button>
              <label className="field">
                Follow-up reminder
                <input type="datetime-local" />
              </label>
              <p className="dim" style={{ fontSize: "0.75rem" }}>
                Reminder scheduling saves locally for now — notifications hook up next.
              </p>
              <a href={selected.job.url} target="_blank" rel="noreferrer" className="btn btn-secondary">
                Open JD
              </a>
            </div>
          </aside>
        </>
      ) : null}
    </div>
  );
}
