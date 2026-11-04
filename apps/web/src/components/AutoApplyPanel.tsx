import { autoApplyBadge, canPrepare, isAutoApplyBusy, PROVIDER_LABEL } from "../autoApply";
import type { ApplicationItem } from "../types";

type Props = {
  item: ApplicationItem;
  busy: boolean;
  onPrepare: () => void;
  onApprove: () => void;
  onCancel: () => void;
};

const CLOSED = ["applied", "interviewing", "offered", "rejected", "withdrawn"];

export function AutoApplyPanel({ item, busy, onPrepare, onApprove, onCancel }: Props) {
  const state = item.auto_apply_state;
  const plan = item.auto_apply_plan;
  const badge = autoApplyBadge(state);
  const working = isAutoApplyBusy(state);
  const provider = item.auto_apply_provider
    ? PROVIDER_LABEL[item.auto_apply_provider] ?? item.auto_apply_provider
    : null;

  if (!state && CLOSED.includes(item.status)) return null;

  return (
    <section className="aa-panel" aria-label="Auto-apply">
      <div className="row space-between" style={{ alignItems: "center" }}>
        <div className="block-label" style={{ margin: 0 }}>
          Auto-apply
        </div>
        {badge ? <span className={`aa-pill ${badge.tone}`} style={{ margin: 0 }}>{badge.label}</span> : null}
      </div>

      {!state ? (
        <p className="muted" style={{ fontSize: "0.8rem" }}>
          The agent finds this job's Greenhouse, Lever or Ashby form and fills it from your
          profile and resume. You review everything before anything is sent.
        </p>
      ) : null}

      {working ? (
        <p className="muted" style={{ fontSize: "0.8rem" }}>
          {state === "submitting"
            ? "Submitting your approved application and waiting for the employer's confirmation…"
            : "Opening the application form and filling it in — usually under a minute…"}
        </p>
      ) : null}

      {item.auto_apply_error && !working ? <p className="aa-error">{item.auto_apply_error}</p> : null}

      {plan && plan.filled.length > 0 && state !== "unsupported" && !working ? (
        <>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            {state === "submitted" ? "Sent" : "Will send"} via {provider ?? "ATS"}
            {item.auto_apply_url ? (
              <>
                {" · "}
                <a href={item.auto_apply_url} target="_blank" rel="noreferrer">
                  open form
                </a>
              </>
            ) : null}
          </div>
          <table className="aa-fields">
            <tbody>
              {plan.filled
                .filter((f) => f.key !== "cover_letter")
                .map((f) => (
                  <tr key={f.key}>
                    <td>{f.label}</td>
                    <td>{f.value}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          {plan.filled.some((f) => f.key === "cover_letter") && plan.cover_letter ? (
            <div>
              <div className="block-label">Cover letter / additional info</div>
              <p className="match-details-text" style={{ fontSize: "0.78rem" }}>
                {plan.cover_letter}
              </p>
            </div>
          ) : null}
          {plan.resume_source === "text_fallback" ? (
            <p className="aa-error">
              Your original resume file isn't stored, so a plain-text copy will be uploaded.
              Re-upload your resume on the Resume page for a better result.
            </p>
          ) : null}
        </>
      ) : null}

      {plan && plan.unanswered_required.length > 0 && !working ? (
        <div>
          <div className="block-label">Questions only you can answer</div>
          <ul className="gap-list">
            {plan.unanswered_required.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state === "submitted" && plan?.submitted_at ? (
        <p className="muted" style={{ fontSize: "0.78rem" }}>
          Submitted {new Date(plan.submitted_at).toLocaleString()}
          {plan.confirmation ? ` · “${plan.confirmation}”` : ""}
        </p>
      ) : null}

      <div className="chip-row">
        {state === "awaiting_approval" ? (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  `Send this application to ${item.job.company} now? This can't be undone.`,
                )
              ) {
                onApprove();
              }
            }}
          >
            Approve &amp; send
          </button>
        ) : null}
        {state === "needs_manual" && item.auto_apply_url ? (
          <a className="btn btn-secondary btn-sm" href={item.auto_apply_url} target="_blank" rel="noreferrer">
            Finish on {provider ?? "employer site"}
          </a>
        ) : null}
        {canPrepare(state) && !CLOSED.includes(item.status) ? (
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={onPrepare}>
            {state ? "Try again" : "Prepare auto-apply"}
          </button>
        ) : null}
        {state && state !== "submitted" && state !== "submitting" ? (
          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={onCancel}>
            Discard
          </button>
        ) : null}
      </div>
    </section>
  );
}
