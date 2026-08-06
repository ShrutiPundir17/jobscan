import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, clearToken } from "./api";
import { ParsedResumePanel } from "./ParsedResumePanel";
import type { ResumeItem, UserProfile } from "./types";

type Props = {
  onLogout: () => void;
  hideChrome?: boolean;
};

function TagInput({
  label,
  values,
  onChange,
  placeholder,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");

  function addTag() {
    const value = draft.trim();
    if (!value) return;
    if (values.some((v) => v.toLowerCase() === value.toLowerCase())) {
      setDraft("");
      return;
    }
    onChange([...values, value]);
    setDraft("");
  }

  return (
    <label className="field">
      {label}
      <div className="chip-row" style={{ marginBottom: "0.45rem" }}>
        {values.map((value) => (
          <span className="chip" key={value}>
            {value}
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((v) => v !== value))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row">
        <input
          type="text"
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addTag();
            }
          }}
        />
        <button type="button" className="btn btn-soft" onClick={addTag}>
          Add
        </button>
      </div>
    </label>
  );
}

export function ProfileDashboard({ onLogout, hideChrome = false }: Props) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [fullName, setFullName] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [locations, setLocations] = useState<string[]>([]);
  const [autoApply, setAutoApply] = useState(false);
  const [minScore, setMinScore] = useState(70);
  const [phone, setPhone] = useState("");
  const [notifyEmail, setNotifyEmail] = useState(true);
  const [notifyWhatsapp, setNotifyWhatsapp] = useState(false);
  const [testingNotify, setTestingNotify] = useState(false);
  const [resumes, setResumes] = useState<ResumeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyResumeId, setBusyResumeId] = useState<string | null>(null);
  const [expandedResumeId, setExpandedResumeId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [me, resumeList] = await Promise.all([api.me(), api.listResumes()]);
      setProfile(me);
      setFullName(me.full_name ?? "");
      setRoles(me.target_roles ?? []);
      setLocations(me.preferred_locations ?? []);
      setAutoApply(me.auto_apply_enabled);
      setMinScore(me.min_match_score);
      setPhone(me.phone ?? "");
      setNotifyEmail(me.notify_email_enabled ?? true);
      setNotifyWhatsapp(me.notify_whatsapp_enabled ?? false);
      setResumes(resumeList.items);
      const firstParsed = resumeList.items.find((r) => r.parsed_data)?.id ?? null;
      setExpandedResumeId(firstParsed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load profile");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const primaryResume = useMemo(
    () => resumes.find((r) => r.is_primary) ?? resumes[0] ?? null,
    [resumes],
  );

  const firstName = useMemo(() => {
    const name = fullName.trim() || profile?.full_name?.trim() || "";
    if (!name) return null;
    return name.split(/\s+/)[0];
  }, [fullName, profile?.full_name]);

  const resumeReady = Boolean(primaryResume?.parsed_data && primaryResume?.has_embedding);

  async function savePreferences(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const updated = await api.updatePreferences({
        full_name: fullName.trim() || null,
        auto_apply_enabled: autoApply,
        min_match_score: minScore,
        target_roles: roles,
        preferred_locations: locations,
        phone: phone.trim() || null,
        notify_email_enabled: notifyEmail,
        notify_whatsapp_enabled: notifyWhatsapp,
      });
      setProfile(updated);
      setPhone(updated.phone ?? "");
      setNotifyEmail(updated.notify_email_enabled);
      setNotifyWhatsapp(updated.notify_whatsapp_enabled);
      setMessage("Preferences saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function onTestNotification() {
    setTestingNotify(true);
    setError(null);
    setMessage(null);
    try {
      await api.updatePreferences({
        phone: phone.trim() || null,
        notify_email_enabled: notifyEmail,
        notify_whatsapp_enabled: notifyWhatsapp,
      });
      const result = await api.sendTestNotification();
      if (result.status !== "ok") {
        setError(result.reason || "Test notification failed");
        return;
      }
      setMessage(
        `Test alert result — Email: ${result.email ?? "n/a"}. WhatsApp: ${result.whatsapp ?? "n/a"}. ${result.hint ?? ""}`.trim(),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test notification failed");
    } finally {
      setTestingNotify(false);
    }
  }

  async function onUpload(file: File | null) {
    if (!file) return;
    setError(null);
    setMessage(null);
    setBusyResumeId("upload");
    try {
      const uploaded = await api.uploadResume(file);
      setResumes((prev) => [uploaded, ...prev]);
      setMessage(`Uploaded ${uploaded.filename ?? "resume"}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusyResumeId(null);
    }
  }

  async function onParse(id: string) {
    setBusyResumeId(id);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.parseResume(id);
      setResumes((prev) => prev.map((r) => (r.id === id ? updated : r)));
      setExpandedResumeId(id);
      setMessage("Resume parsed and embedded. Scroll to see extracted details.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Parse failed");
    } finally {
      setBusyResumeId(null);
    }
  }

  async function onEmbed(id: string) {
    setBusyResumeId(id);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.embedResume(id);
      setResumes((prev) => prev.map((r) => (r.id === id ? updated : r)));
      setMessage("Embedding refreshed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Embed failed");
    } finally {
      setBusyResumeId(null);
    }
  }

  function logout() {
    clearToken();
    onLogout();
  }

  if (loading) {
    if (hideChrome) {
      return <p className="muted">Warming up your workspace…</p>;
    }
    return (
      <div className="app-shell loading-shell">
        <div>
          <p className="loading-mark brand">
            Job<span>Agent</span>
          </p>
          <p className="muted">Warming up your workspace…</p>
          <div className="loading-bar" aria-hidden>
            <span />
          </div>
        </div>
      </div>
    );
  }

  const header = !hideChrome ? (
    <header className="dashboard-top">
      <div>
        <p className="brand">
          Job<span>Agent</span>
        </p>
        <p className="dashboard-kicker">
          <span className="pulse-dot" aria-hidden />
          Search cockpit
        </p>
        <h1 className="dashboard-title">
          {firstName ? (
            <>
              Ready when you are, <em>{firstName}</em>
            </>
          ) : (
            <>Tune your search profile</>
          )}
        </h1>
        <p className="muted" style={{ margin: 0, maxWidth: "36rem" }}>
          Set the roles you want, drop in a resume, and let JobAgent hunt while you focus.
        </p>
      </div>
      <button type="button" className="btn btn-ghost" onClick={logout}>
        Log out
      </button>
    </header>
  ) : (
    <header style={{ marginBottom: "1rem" }}>
      <h1 className="dashboard-title" style={{ marginBottom: "0.35rem" }}>
        {firstName ? (
          <>
            Ready when you are, <em>{firstName}</em>
          </>
        ) : (
          <>Tune your search profile</>
        )}
      </h1>
      <p className="muted" style={{ margin: 0, maxWidth: "36rem" }}>
        Set the roles you want, drop in a resume, and let JobAgent hunt while you focus.
      </p>
    </header>
  );

  const content = (
    <>
      {header}

      <div className="ready-strip" aria-label="Profile readiness">
        <span className="ready-chip">
          Roles <strong>{roles.length || "—"}</strong>
        </span>
        <span className="ready-chip">
          Locations <strong>{locations.length || "—"}</strong>
        </span>
        <span className="ready-chip">
          Resume{" "}
          <strong>{resumeReady ? "matched-ready" : primaryResume ? "needs parse" : "upload"}</strong>
        </span>
        <span className="ready-chip">
          Min score <strong>{minScore}</strong>
        </span>
      </div>

      {error ? <div className="flash error fade-in">{error}</div> : null}
      {message ? <div className="flash success fade-in">{message}</div> : null}

      <div className="grid-2">
        <form className="panel panel-pad stack fade-in delay-1" onSubmit={savePreferences}>
          <div>
            <h2 className="section-title">Preferences</h2>
            <p className="section-copy">
              Tell JobAgent what “good” looks like — roles, cities, and how aggressive to apply.
            </p>
          </div>

          <label className="field">
            Full name
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Your name"
            />
          </label>

          <label className="field">
            Email
            <input type="email" value={profile?.email ?? ""} disabled />
          </label>

          <TagInput
            label="Target roles"
            values={roles}
            onChange={setRoles}
            placeholder="e.g. Backend Engineer"
          />

          <TagInput
            label="Preferred locations"
            values={locations}
            onChange={setLocations}
            placeholder="e.g. Remote, Bangalore"
          />

          <label className="toggle">
            <input
              type="checkbox"
              checked={autoApply}
              onChange={(e) => setAutoApply(e.target.checked)}
            />
            Auto-apply when match score is high enough
          </label>

          <label className="field">
            Minimum match score · {minScore}
            <input
              type="range"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
            />
          </label>

          <div
            style={{
              borderTop: "1px solid color-mix(in srgb, var(--ink) 12%, transparent)",
              paddingTop: "1rem",
              marginTop: "0.25rem",
            }}
          >
            <h3 className="section-title" style={{ fontSize: "1.05rem", marginBottom: "0.35rem" }}>
              Notifications
            </h3>
            <p className="section-copy" style={{ marginTop: 0 }}>
              Saving preferences only stores your choices. Alerts go out when strong matches are
              found — or click <strong>Send test alert</strong> to verify Email/WhatsApp now.
            </p>

            <label className="toggle">
              <input
                type="checkbox"
                checked={notifyEmail}
                onChange={(e) => setNotifyEmail(e.target.checked)}
              />
              Email me at {profile?.email ?? "my account email"}
            </label>

            <label className="toggle">
              <input
                type="checkbox"
                checked={notifyWhatsapp}
                onChange={(e) => setNotifyWhatsapp(e.target.checked)}
              />
              WhatsApp me when matches are found
            </label>

            <label className="field">
              WhatsApp / mobile number
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 98XXXXXXXX"
                disabled={!notifyWhatsapp}
              />
            </label>
            {notifyWhatsapp && !phone.trim() ? (
              <p className="muted" style={{ margin: 0 }}>
                Add a number with country code so WhatsApp alerts can be delivered.
              </p>
            ) : null}

            <div className="row" style={{ marginTop: "0.5rem" }}>
              <button
                type="button"
                className="btn btn-soft"
                disabled={testingNotify}
                onClick={() => void onTestNotification()}
              >
                {testingNotify ? "Sending test…" : "Send test alert"}
              </button>
            </div>
          </div>

          <div className="row">
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save preferences"}
            </button>
          </div>
        </form>

        <section className="panel panel-pad fade-in delay-2">
          <div className="row space-between" style={{ marginBottom: "0.85rem" }}>
            <div>
              <h2 className="section-title" style={{ marginBottom: 0 }}>
                Resume vault
              </h2>
              <p className="section-copy" style={{ marginTop: "0.35rem" }}>
                Upload once. Parse + embed so matching can score real fit.
              </p>
            </div>
            <label className="btn btn-accent" style={{ display: "inline-block" }}>
              {busyResumeId === "upload" ? "Uploading…" : "Upload resume"}
              <input
                type="file"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                hidden
                disabled={busyResumeId === "upload"}
                onChange={(e) => void onUpload(e.target.files?.[0] ?? null)}
              />
            </label>
          </div>

          {primaryResume ? (
            <p className="muted" style={{ marginTop: 0 }}>
              Active file ·{" "}
              <strong style={{ color: "var(--ink)" }}>{primaryResume.filename}</strong>
            </p>
          ) : (
            <p className="muted">
              No resumes yet. Upload a PDF or DOCX, then run <strong>Parse + embed</strong>.
            </p>
          )}

          <div>
            {resumes.map((resume) => (
              <article className="resume-item" key={resume.id}>
                <div className="row space-between">
                  <div>
                    <div className="resume-name">{resume.filename ?? "Untitled resume"}</div>
                    {resume.is_primary ? (
                      <span className="status-pill ok" style={{ marginTop: "0.35rem" }}>
                        Primary
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="row">
                  <span className={`status-pill ${resume.parsed_data ? "ok" : "warn"}`}>
                    {resume.parsed_data ? "Parsed" : "Not parsed"}
                  </span>
                  <span className={`status-pill ${resume.has_embedding ? "ok" : "warn"}`}>
                    {resume.has_embedding ? "Embedded" : "No embedding"}
                  </span>
                </div>
                <div className="row">
                  <button
                    type="button"
                    className="btn btn-soft"
                    disabled={busyResumeId === resume.id}
                    onClick={() => void onParse(resume.id)}
                  >
                    {busyResumeId === resume.id ? "Working…" : "Parse + embed"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={busyResumeId === resume.id}
                    onClick={() => void onEmbed(resume.id)}
                  >
                    Refresh embedding
                  </button>
                  {resume.parsed_data ? (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() =>
                        setExpandedResumeId((curr) =>
                          curr === resume.id ? null : resume.id,
                        )
                      }
                    >
                      {expandedResumeId === resume.id ? "Hide parsed data" : "View parsed data"}
                    </button>
                  ) : null}
                </div>
                {expandedResumeId === resume.id && resume.parsed_data ? (
                  <ParsedResumePanel data={resume.parsed_data} />
                ) : null}
              </article>
            ))}
          </div>
        </section>
      </div>
    </>
  );

  if (hideChrome) {
    return <div className="dashboard-inner">{content}</div>;
  }
  return <div className="app-shell">{content}</div>;
}
