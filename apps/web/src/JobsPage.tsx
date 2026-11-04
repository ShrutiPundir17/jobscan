import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api";
import { postedLabel } from "./components/MatchCard";
import type { JobDetail, JobListItem, MyApplicationSummary } from "./types";

type Props = {
  /** Query submitted from the top-bar search; `querySeq` changes on every submit. */
  query: string;
  querySeq: number;
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

const SOURCES = ["linkedin", "naukri", "internshala", "foundit", "unstop"] as const;
const PAGE = 20;

const STATUS_LABEL: Record<string, string> = {
  pending_review: "Matched",
  matched: "Matched",
  discovered: "Saved",
  applying: "Applying",
  applied: "Applied",
  interviewing: "Interviewing",
  offered: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  failed: "Failed",
};

function myStatus(app: MyApplicationSummary | null): string | null {
  if (!app) return null;
  const label = STATUS_LABEL[app.status] ?? app.status;
  return app.match_score != null ? `${label} · ${app.match_score}` : label;
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export function JobsPage({ query, querySeq, onError }: Props) {
  const [q, setQ] = useState(query);
  const [debouncedQ, setDebouncedQ] = useState(query);
  const [source, setSource] = useState<string>("");
  const [myLocations, setMyLocations] = useState(false);
  const [items, setItems] = useState<JobListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);
  // Bumped whenever the result set is replaced; stale "load more" pages are dropped.
  const generation = useRef(0);

  // A new top-bar search replaces whatever is typed here (even the same text again).
  useEffect(() => {
    setQ(query);
    setDebouncedQ(query);
  }, [query, querySeq]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  // Every filter change starts a fresh first page; older requests are cancelled
  // so a slow response can never overwrite a newer one.
  useEffect(() => {
    inflight.current?.abort();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    generation.current += 1;
    setLoading(true);
    onError(null);
    api
      .listJobs({ q: debouncedQ, source, myLocations, limit: PAGE, offset: 0 }, ctrl.signal)
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((err) => {
        if (!isAbort(err)) onError(err instanceof Error ? err.message : "Could not load jobs");
      })
      .finally(() => {
        if (inflight.current === ctrl) setLoading(false);
      });
    return () => ctrl.abort();
  }, [debouncedQ, source, myLocations]);

  async function loadMore() {
    setLoadingMore(true);
    const gen = generation.current;
    try {
      const res = await api.listJobs({ q: debouncedQ, source, myLocations, limit: PAGE, offset: items.length });
      if (gen !== generation.current) return; // filters changed meanwhile
      // New scrapes can shift offsets between pages; never show a job twice.
      setItems((prev) => {
        const seen = new Set(prev.map((j) => j.id));
        return [...prev, ...res.items.filter((j) => !seen.has(j.id))];
      });
      setTotal(res.total);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not load more jobs");
    } finally {
      setLoadingMore(false);
    }
  }

  async function open(job: JobListItem) {
    setDetailLoading(job.id);
    try {
      setDetail(await api.getJob(job.id));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not open job");
    } finally {
      setDetailLoading(null);
    }
  }

  return (
    <div className="fade-in">
      <div className="row space-between" style={{ marginBottom: "1rem", flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: "1.35rem" }}>Jobs</h1>
          <p className="muted" style={{ fontSize: "0.875rem" }}>
            {loading ? "Searching…" : `${total} job${total === 1 ? "" : "s"} scanned by your agent`}
          </p>
        </div>
        <input
          type="search"
          className="jobs-search"
          placeholder="Title, company or city"
          value={q}
          maxLength={100}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search jobs"
        />
      </div>

      <div className="filter-bar">
        <button type="button" className={`filter-chip ${source === "" ? "active" : ""}`} onClick={() => setSource("")}>
          All
        </button>
        {SOURCES.map((s) => (
          <button
            key={s}
            type="button"
            className={`filter-chip ${source === s ? "active" : ""}`}
            onClick={() => setSource(s)}
          >
            {s[0].toUpperCase() + s.slice(1)}
          </button>
        ))}
        <button
          type="button"
          className={`filter-chip ${myLocations ? "active" : ""}`}
          aria-pressed={myLocations}
          onClick={() => setMyLocations((v) => !v)}
        >
          My locations
        </button>
      </div>

      {loading && items.length === 0 ? (
        <p className="muted">Loading jobs…</p>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <div className="empty-illu">NO JOBS</div>
          <p className="muted">
            {debouncedQ || source || myLocations
              ? "Nothing matches these filters."
              : "No jobs scanned yet — run Scan jobs from the Matches page."}
          </p>
        </div>
      ) : (
        <>
          <div className={`stack ${loading ? "is-refreshing" : ""}`} style={{ gap: "0.5rem" }}>
            {items.map((j) => {
              const status = myStatus(j.my_application);
              return (
                <button type="button" className="job-row" key={j.id} onClick={() => void open(j)}>
                  <div style={{ minWidth: 0 }}>
                    <div className="job-row-title">{j.title}</div>
                    <div className="muted" style={{ fontSize: "0.8rem" }}>
                      {j.company} · {j.location?.trim() || "Location not listed"} · via {j.source} ·{" "}
                      {postedLabel(j.posted_at ?? j.created_at)}
                    </div>
                    {j.description_snippet ? <p className="job-row-snippet">{j.description_snippet}</p> : null}
                  </div>
                  <div className="job-row-side">
                    {status ? <span className="aa-pill done" style={{ margin: 0 }}>{status}</span> : null}
                    {detailLoading === j.id ? <span className="muted">…</span> : null}
                  </div>
                </button>
              );
            })}
          </div>
          {items.length < total ? (
            <div style={{ textAlign: "center", marginTop: "1rem" }}>
              <button type="button" className="btn btn-secondary" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? "Loading…" : `Load more (${total - items.length} left)`}
              </button>
            </div>
          ) : null}
        </>
      )}

      {detail
        ? createPortal(
            <>
              <div className="drawer-scrim" onClick={() => setDetail(null)} />
              <aside className="drawer" aria-label="Job detail">
                <div className="drawer-head">
                  <strong>{detail.company}</strong>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDetail(null)}>
                    Close
                  </button>
                </div>
                <div className="drawer-body stack">
                  <div>
                    <div style={{ fontWeight: 600 }}>{detail.title}</div>
                    <div className="muted" style={{ fontSize: "0.8rem" }}>
                      {detail.location?.trim() || "Location not listed"} · via {detail.source} ·{" "}
                      {postedLabel(detail.posted_at ?? detail.created_at)}
                    </div>
                    {myStatus(detail.my_application) ? (
                      <span className="aa-pill done" style={{ marginLeft: 0 }}>{myStatus(detail.my_application)}</span>
                    ) : (
                      <p className="muted" style={{ fontSize: "0.78rem", marginTop: 6 }}>
                        Not in your matches yet — Find matches scores jobs like this against your resume.
                      </p>
                    )}
                  </div>
                  <a className="btn btn-primary" href={detail.url} target="_blank" rel="noreferrer noopener">
                    Open job posting
                  </a>
                  <div>
                    <div className="block-label">Job description</div>
                    {detail.description ? (
                      <p className="match-details-text">{detail.description}</p>
                    ) : (
                      <p className="muted" style={{ fontSize: "0.8rem" }}>
                        No description was captured — open the posting for full details.
                      </p>
                    )}
                  </div>
                </div>
              </aside>
            </>,
            document.body,
          )
        : null}
    </div>
  );
}
