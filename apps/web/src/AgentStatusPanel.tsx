import { useEffect, useState } from "react";
import { api } from "./api";

type AgentStatus = {
  state: string;
  scanner_enabled: boolean;
  last_scan_at: string | null;
  jobs_scanned_today: number;
  high_match_count: number;
  high_match_threshold: number;
  server_time: string;
};

function formatLocalClock(date: Date): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const tz = get("timeZoneName") || "local";
  return `${get("hour")}:${get("minute")}:${get("second")} ${tz}`;
}

function formatLastScan(iso: string | null, nowMs: number): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const sec = Math.max(0, Math.floor((nowMs - then) / 1000));
  if (sec < 45) return "just now";
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    return m <= 1 ? "1 min ago" : `${m} min ago`;
  }
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    return h === 1 ? "1 hr ago" : `${h} hrs ago`;
  }
  const d = Math.floor(sec / 86400);
  return d === 1 ? "1 day ago" : `${d} days ago`;
}

function formatCount(n: number): string {
  return n.toLocaleString("en-IN");
}

export function AgentStatusPanel() {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await api.agentStatus();
        if (!cancelled) {
          setStatus(res);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Status unavailable");
        }
      }
    }

    void load();
    const poll = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
    };
  }, []);

  useEffect(() => {
    const tick = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(tick);
  }, []);

  const rows: { label: string; value: string; tone?: "ok" | "warn" | "dim" }[] = [
    {
      label: "State",
      value: status?.state ?? (error ? "offline" : "…"),
      tone:
        status?.state === "active"
          ? "ok"
          : status?.state === "paused" || status?.state === "degraded" || error
            ? "warn"
            : "dim",
    },
    {
      label: "Last scan",
      value: status ? formatLastScan(status.last_scan_at, now.getTime()) : "…",
    },
    {
      label: "Scanned today",
      value: status ? formatCount(status.jobs_scanned_today) : "…",
    },
    {
      label: `Matches ≥ ${status?.high_match_threshold ?? 85}`,
      value: status ? formatCount(status.high_match_count) : "…",
    },
    {
      label: "Local time",
      value: formatLocalClock(now),
    },
  ];

  return (
    <aside className="agent-status" aria-live="polite" aria-label="Agent status">
      <div className="agent-status-head">
        <span className="agent-status-title">Agent status</span>
        <span
          className={`agent-status-dot ${
            status?.state === "active"
              ? "on"
              : error || status?.state === "degraded"
                ? "err"
                : "idle"
          }`}
          aria-hidden
        />
      </div>
      <dl className="agent-status-rows">
        {rows.map((row) => (
          <div className="agent-status-row" key={row.label}>
            <dt>{row.label}</dt>
            <dd className={row.tone ? `tone-${row.tone}` : undefined}>{row.value}</dd>
          </div>
        ))}
      </dl>
      {error ? <p className="agent-status-err">{error}</p> : null}
    </aside>
  );
}
