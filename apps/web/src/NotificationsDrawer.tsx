import { useEffect, useState } from "react";
import { api } from "./api";

type Notif = {
  id: string;
  type: string;
  title: string;
  body: string;
  read_at: string | null;
  created_at: string;
};

type Props = {
  open: boolean;
  onClose: () => void;
  onChanged?: (unread: number) => void;
};

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 1) return "Just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function iconTone(type: string): string {
  if (/match|success|applied/i.test(type)) return "success";
  if (/warn|fail|error|sync/i.test(type)) return "warn";
  return "";
}

export function NotificationsDrawer({ open, onClose, onChanged }: Props) {
  const [items, setItems] = useState<Notif[]>([]);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await api.listNotifications();
      setItems(res.items);
      onChanged?.(res.unread_count);
    } catch {
      /* keep empty */
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) void load();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" aria-label="Notifications">
        <div className="drawer-head">
          <strong>Notifications</strong>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="drawer-body">
          {loading ? <p className="muted">Loading…</p> : null}
          {!loading && items.length === 0 ? (
            <div className="empty-state" style={{ border: "none" }}>
              <p className="muted">No new updates — your agent is on the hunt.</p>
            </div>
          ) : null}
          {items.map((n) => (
            <div className={`notify-item ${n.read_at ? "" : "unread"}`} key={n.id}>
              <div className={`notify-icon ${iconTone(n.type)}`}>
                {n.type.slice(0, 1).toUpperCase()}
              </div>
              <div>
                <div style={{ fontSize: "0.875rem", fontWeight: n.read_at ? 400 : 600 }}>
                  {n.title}
                </div>
                {n.body ? (
                  <p className="muted" style={{ fontSize: "0.8rem", marginTop: 2 }}>
                    {n.body}
                  </p>
                ) : null}
                <div className="notify-time">{relativeTime(n.created_at)}</div>
                {!n.read_at ? (
                  <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    style={{ marginTop: 8 }}
                    onClick={() =>
                      void api.markNotificationRead(n.id).then(() => load())
                    }
                  >
                    Mark read
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
