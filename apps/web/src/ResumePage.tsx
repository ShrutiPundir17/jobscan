import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { ScoreGauge } from "./components/ScoreGauge";
import type { ParsedResumeData, PersistedMatch, ResumeItem } from "./types";

type Props = {
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

function healthFromResume(data: ParsedResumeData | null, hasEmbed: boolean): number {
  if (!data) return 0;
  let score = 40;
  if ((data.skills?.length ?? 0) >= 6) score += 15;
  else if ((data.skills?.length ?? 0) >= 3) score += 8;
  if ((data.experience?.length ?? 0) >= 1) score += 15;
  if ((data.education?.length ?? 0) >= 1) score += 10;
  if (data.summary) score += 10;
  if (hasEmbed) score += 10;
  return Math.min(100, score);
}

export function ResumePage({ onMessage, onError }: Props) {
  const [resumes, setResumes] = useState<ResumeItem[]>([]);
  const [tailored, setTailored] = useState<PersistedMatch[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const [r, m] = await Promise.all([api.listResumes(), api.listMatches()]);
      setResumes(r.items);
      const withTailor = m.items.filter((x) => x.tailored_resume_text);
      setTailored(withTailor);
      const primary = r.items.find((x) => x.is_primary) ?? r.items[0] ?? null;
      setSelectedId(primary?.id ?? null);
      setPreviewText(primary?.raw_text || primary?.filename || "");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to load resume");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const primary = useMemo(
    () => resumes.find((r) => r.id === selectedId) ?? resumes[0] ?? null,
    [resumes, selectedId],
  );

  const parsed = (primary?.parsed_data as ParsedResumeData | null) ?? null;
  const health = healthFromResume(parsed, Boolean(primary?.has_embedding));

  async function onUpload(file: File | null) {
    if (!file) return;
    setBusy(true);
    onError(null);
    try {
      const uploaded = await api.uploadResume(file);
      const parsedR = await api.parseResume(uploaded.id);
      const embedded = await api.embedResume(parsedR.id);
      setResumes((prev) => [embedded, ...prev]);
      setSelectedId(embedded.id);
      setPreviewText(embedded.raw_text || embedded.filename || "");
      onMessage(`Uploaded and parsed ${embedded.filename ?? "resume"}.`);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">Loading resume…</p>;

  return (
    <div className="fade-in">
      <div className="section-head">
        <h1 style={{ fontSize: "1.35rem", textTransform: "none", letterSpacing: "-0.02em" }}>
          Resume
        </h1>
        <label className="btn btn-primary btn-sm" style={{ cursor: busy ? "wait" : "pointer" }}>
          {busy ? "Uploading…" : "Upload new"}
          <input
            type="file"
            accept=".pdf,.docx"
            hidden
            disabled={busy}
            onChange={(e) => void onUpload(e.target.files?.[0] ?? null)}
          />
        </label>
      </div>

      {!primary ? (
        <div className="empty-state">
          <div className="empty-illu">RESUME</div>
          <p>No resume found — upload to start hunting.</p>
        </div>
      ) : (
        <div className="resume-layout">
          <div className="resume-preview">
            <div className="block-label" style={{ marginBottom: 10 }}>
              Preview
            </div>
            <div className="resume-doc">
              {previewText || parsed?.summary || "No text preview available for this file."}
            </div>
          </div>

          <div className="resume-side">
            <div className="panel-block">
              <div className="block-label" style={{ marginBottom: 12 }}>
                Resume health score
              </div>
              <div className="health-row">
                <ScoreGauge score={health} size={96} />
                <div style={{ flex: 1 }}>
                  <div
                    className="progress-track"
                    style={{ marginTop: 0, marginBottom: 8 }}
                  >
                    <div
                      className="progress-fill"
                      style={{
                        width: `${health}%`,
                        background:
                          health >= 80
                            ? "var(--success)"
                            : health >= 60
                              ? "var(--warning)"
                              : "var(--danger)",
                      }}
                    />
                  </div>
                  <p className="muted" style={{ fontSize: "0.8rem" }}>
                    Based on ATS signals: skills, experience, education, and embedding
                    readiness.
                  </p>
                  {health < 80 ? (
                    <p style={{ fontSize: "0.8rem", color: "var(--warning)", marginTop: 6 }}>
                      Tip: add metrics to bullets and ensure key tech keywords appear.
                    </p>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="panel-block">
              <div className="block-label" style={{ marginBottom: 10 }}>
                Extracted skills
              </div>
              <div className="chip-row">
                {(parsed?.skills ?? []).slice(0, 16).map((s) => (
                  <span className="chip" key={s}>
                    {s}
                  </span>
                ))}
                {!parsed?.skills?.length ? (
                  <span className="muted">No skills extracted yet — parse your resume.</span>
                ) : null}
              </div>
            </div>

            <div className="panel-block">
              <div className="block-label" style={{ marginBottom: 10 }}>
                Experience
              </div>
              <div className="stack" style={{ gap: "0.55rem" }}>
                {(parsed?.experience ?? []).slice(0, 4).map((exp, i) => (
                  <div key={`${exp.company}-${i}`}>
                    <strong style={{ fontSize: "0.85rem" }}>
                      {exp.title} · {exp.company}
                    </strong>
                    <div className="muted" style={{ fontSize: "0.75rem" }}>
                      {[exp.start_date, exp.end_date].filter(Boolean).join(" → ") || "—"}
                    </div>
                  </div>
                ))}
                {!parsed?.experience?.length ? (
                  <span className="muted">No experience blocks yet.</span>
                ) : null}
              </div>
            </div>

            <div className="panel-block">
              <div className="block-label" style={{ marginBottom: 10 }}>
                Tailored versions
              </div>
              {tailored.length === 0 ? (
                <p className="muted" style={{ fontSize: "0.85rem" }}>
                  No tailored versions yet — Apply from a match to generate one.
                </p>
              ) : (
                <div className="stack" style={{ gap: "0.45rem" }}>
                  {tailored.map((t) => (
                    <button
                      type="button"
                      className={`version-row ${
                        previewText === t.tailored_resume_text ? "active" : ""
                      }`}
                      key={t.id}
                      onClick={() => setPreviewText(t.tailored_resume_text || "")}
                    >
                      <span>
                        <strong style={{ fontSize: "0.85rem" }}>{t.job.title}</strong>
                        <div className="muted" style={{ fontSize: "0.75rem" }}>
                          {t.job.company}
                        </div>
                      </span>
                      <span className="mono muted">{t.match_score ?? "—"}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
