import { useMemo, useState } from "react";
import { api } from "./api";
import { TagInput } from "./components/TagInput";
import type { ParsedResumeData, ResumeItem } from "./types";

const ONBOARD_KEY = "jobagent_onboarded";
const PORTALS_KEY = "jobagent_portals";

export function isOnboarded(): boolean {
  return localStorage.getItem(ONBOARD_KEY) === "1";
}

export function markOnboarded(): void {
  localStorage.setItem(ONBOARD_KEY, "1");
}

const PORTALS = [
  { id: "naukri", label: "Naukri", short: "NK" },
  { id: "linkedin", label: "LinkedIn", short: "in" },
  { id: "internshala", label: "Internshala", short: "IS" },
  { id: "foundit", label: "Foundit", short: "FI" },
  { id: "unstop", label: "Unstop", short: "UN" },
  { id: "indeed", label: "Indeed", short: "ID" },
] as const;

type Props = {
  onDone: () => void;
};

export function OnboardingFlow({ onDone }: Props) {
  const [step, setStep] = useState(1);
  const [resume, setResume] = useState<ResumeItem | null>(null);
  const [skills, setSkills] = useState<string[]>([]);
  const [progress, setProgress] = useState(0);
  const [parsing, setParsing] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [roles, setRoles] = useState<string[]>([]);
  const [locations, setLocations] = useState<string[]>([]);
  const [experience, setExperience] = useState("Junior");
  const [minScore, setMinScore] = useState(70);
  const [autoApply, setAutoApply] = useState(false);
  const [portals, setPortals] = useState<Record<string, boolean>>(() => {
    try {
      const raw = localStorage.getItem(PORTALS_KEY);
      if (raw) return JSON.parse(raw) as Record<string, boolean>;
    } catch {
      /* ignore */
    }
    return {
      linkedin: true,
      internshala: true,
      naukri: true,
      foundit: false,
      unstop: true,
      indeed: false,
    };
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const canContinueStep1 = Boolean(resume?.parsed_data) && !parsing;
  const portalConnected = useMemo(
    () => Object.values(portals).some(Boolean),
    [portals],
  );

  async function handleFile(file: File | null) {
    if (!file) return;
    setError(null);
    setParsing(true);
    setProgress(12);
    try {
      const uploaded = await api.uploadResume(file);
      setProgress(40);
      const parsed = await api.parseResume(uploaded.id);
      setProgress(75);
      const embedded = await api.embedResume(parsed.id);
      setProgress(100);
      setResume(embedded);
      const data = embedded.parsed_data as ParsedResumeData | null;
      setSkills((data?.skills ?? []).slice(0, 12));
      if (data?.seniority) {
        const s = String(data.seniority).toLowerCase();
        if (s.includes("intern")) setExperience("Intern");
        else if (s.includes("senior")) setExperience("Senior");
        else if (s.includes("mid")) setExperience("Mid");
        else setExperience("Junior");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setProgress(0);
    } finally {
      setParsing(false);
    }
  }

  async function saveStep2AndNext() {
    setSaving(true);
    setError(null);
    try {
      await api.updatePreferences({
        target_roles: roles,
        preferred_locations: locations,
        min_match_score: minScore,
        auto_apply_enabled: autoApply,
      });
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save preferences");
    } finally {
      setSaving(false);
    }
  }

  function finish() {
    if (!portalConnected) {
      setError("Connect at least one portal to continue.");
      return;
    }
    localStorage.setItem(PORTALS_KEY, JSON.stringify(portals));
    markOnboarded();
    onDone();
  }

  return (
    <div className="onboard-page">
      <div className="auth-ambient" aria-hidden />
      <div className="onboard-card fade-in">
        <div className="onboard-steps">
          {[
            [1, "Resume"],
            [2, "Preferences"],
            [3, "Portals"],
          ].map(([n, label]) => {
            const num = Number(n);
            const cls =
              step === num ? "active" : step > num ? "done" : "";
            return (
              <div className={`onboard-step ${cls}`} key={num}>
                <span className="dot">{step > num ? "ok" : num}</span>
                <span>{label}</span>
              </div>
            );
          })}
        </div>

        {error ? <div className="flash error" style={{ marginBottom: "1rem" }}>{error}</div> : null}

        {step === 1 ? (
          <div className="stack">
            <div
              className={`dropzone ${dragOver ? "dragover" : ""} ${
                resume?.parsed_data && !parsing ? "success" : ""
              }`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                void handleFile(e.dataTransfer.files?.[0] ?? null);
              }}
              onClick={() => document.getElementById("resume-file")?.click()}
            >
              <p>
                Drag & drop your resume here, or click to{" "}
                <strong style={{ color: "var(--accent)" }}>browse</strong> (PDF or DOCX).
              </p>
              <input
                id="resume-file"
                type="file"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                hidden
                onChange={(e) => void handleFile(e.target.files?.[0] ?? null)}
              />
            </div>
            {(parsing || progress > 0) && (
              <div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${progress}%` }} />
                </div>
                <p className="muted" style={{ fontSize: "0.8rem", marginTop: 6 }}>
                  {parsing
                    ? `Parsing resume… ${progress}%`
                    : resume
                      ? `Ready — ${resume.filename ?? "resume"}`
                      : null}
                </p>
              </div>
            )}
            {skills.length > 0 ? (
              <div>
                <div className="block-label" style={{ marginBottom: 8 }}>
                  Skills detected
                </div>
                <div className="chip-row">
                  {skills.map((s) => (
                    <span className="chip" key={s}>
                      {s}
                      <button
                        type="button"
                        aria-label={`Remove ${s}`}
                        onClick={() => setSkills(skills.filter((x) => x !== s))}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="onboard-actions">
              <span />
              <button
                type="button"
                className="btn btn-primary"
                disabled={!canContinueStep1}
                onClick={() => setStep(2)}
              >
                Continue
              </button>
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="stack">
            <TagInput
              label="Target roles"
              values={roles}
              onChange={setRoles}
              placeholder="e.g. Full Stack Engineer, Backend Developer…"
            />
            <TagInput
              label="Preferred cities"
              values={locations}
              onChange={setLocations}
              placeholder="e.g. Hyderabad, Bangalore, Remote"
            />
            <div>
              <div className="field" style={{ marginBottom: 6 }}>
                Experience level
              </div>
              <div className="segmented">
                {["Intern", "Junior", "Mid", "Senior"].map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={experience === level ? "active" : ""}
                    onClick={() => setExperience(level)}
                  >
                    {level}
                  </button>
                ))}
              </div>
            </div>
            <div className="slider-row">
              <div className="row space-between">
                <span className="field" style={{ margin: 0 }}>
                  Minimum match score
                </span>
                <span className="mono">{minScore}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
              />
            </div>
            <div className="toggle-row">
              <span style={{ fontSize: "0.875rem", color: "var(--text-muted)" }}>
                Automatically apply to matches above your score threshold.
              </span>
              <button
                type="button"
                className={`toggle ${autoApply ? "on" : ""}`}
                aria-pressed={autoApply}
                onClick={() => setAutoApply((v) => !v)}
              />
            </div>
            <div className="onboard-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setStep(1)}>
                Back
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={saving}
                onClick={() => void saveStep2AndNext()}
              >
                {saving ? "Saving…" : "Continue"}
              </button>
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="stack">
            <p className="muted" style={{ fontSize: "0.875rem" }}>
              Toggle portals your agent should scan. At least one required. OAuth for
              each portal can be added later in Settings.
            </p>
            <div className="portal-grid">
              {PORTALS.map((p) => {
                const on = Boolean(portals[p.id]);
                return (
                  <div className={`portal-card ${on ? "connected" : ""}`} key={p.id}>
                    <div className="portal-logo">{p.short}</div>
                    <strong style={{ fontSize: "0.85rem" }}>{p.label}</strong>
                    <span className="portal-status">{on ? "Connected" : "Not connected"}</span>
                    <button
                      type="button"
                      className={`toggle ${on ? "on" : ""}`}
                      aria-label={`Toggle ${p.label}`}
                      onClick={() =>
                        setPortals((prev) => ({ ...prev, [p.id]: !prev[p.id] }))
                      }
                    />
                  </div>
                );
              })}
            </div>
            <div className="onboard-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setStep(2)}>
                Back
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!portalConnected}
                onClick={finish}
              >
                Finish
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
