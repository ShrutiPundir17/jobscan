import type { AutoApplyState } from "./types";

export const AUTO_APPLY_BUSY: AutoApplyState[] = ["queued", "preparing", "submitting", "sending"];

const LABELS: Partial<Record<AutoApplyState, { label: string; tone: string }>> = {
  queued: { label: "Auto-apply queued", tone: "busy" },
  preparing: { label: "Filling form…", tone: "busy" },
  awaiting_approval: { label: "Ready to send", tone: "ready" },
  needs_manual: { label: "Needs manual apply", tone: "warn" },
  unsupported: { label: "Manual apply only", tone: "muted" },
  submitting: { label: "Submitting…", tone: "busy" },
  sending: { label: "Submitting…", tone: "busy" },
  submitted: { label: "Auto-applied", tone: "done" },
  unconfirmed: { label: "Check if received", tone: "warn" },
  failed: { label: "Auto-apply failed", tone: "error" },
  // dismissed: no badge — the user discarded it
};

export function autoApplyBadge(state: AutoApplyState | null | undefined) {
  return state ? LABELS[state] ?? null : null;
}

export function isAutoApplyBusy(state: AutoApplyState | null | undefined): boolean {
  return !!state && AUTO_APPLY_BUSY.includes(state);
}

/** States from which the user can (re)start the agent. Never `unconfirmed`: it may already be sent. */
export function canPrepare(state: AutoApplyState | null | undefined): boolean {
  return (
    !state ||
    state === "dismissed" ||
    state === "failed" ||
    state === "needs_manual" ||
    state === "unsupported"
  );
}

/** True when the user has never run (or discarded) auto-apply for this job. */
export function isFresh(state: AutoApplyState | null | undefined): boolean {
  return !state || state === "dismissed";
}

export const PROVIDER_LABEL: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
};
