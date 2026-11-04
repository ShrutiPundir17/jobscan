import type { AutoApplyState } from "./types";

export const AUTO_APPLY_BUSY: AutoApplyState[] = ["queued", "preparing", "submitting"];

const LABELS: Record<AutoApplyState, { label: string; tone: string }> = {
  queued: { label: "Auto-apply queued", tone: "busy" },
  preparing: { label: "Filling form…", tone: "busy" },
  awaiting_approval: { label: "Ready to send", tone: "ready" },
  needs_manual: { label: "Needs manual apply", tone: "warn" },
  unsupported: { label: "Manual apply only", tone: "muted" },
  submitting: { label: "Submitting…", tone: "busy" },
  submitted: { label: "Auto-applied", tone: "done" },
  failed: { label: "Auto-apply failed", tone: "error" },
};

export function autoApplyBadge(state: AutoApplyState | null | undefined) {
  return state ? LABELS[state] ?? null : null;
}

export function isAutoApplyBusy(state: AutoApplyState | null | undefined): boolean {
  return !!state && AUTO_APPLY_BUSY.includes(state);
}

/** States from which the user can (re)start the agent. */
export function canPrepare(state: AutoApplyState | null | undefined): boolean {
  return !state || state === "failed" || state === "needs_manual" || state === "unsupported";
}

export const PROVIDER_LABEL: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
};
