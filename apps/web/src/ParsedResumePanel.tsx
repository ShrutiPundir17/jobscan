import type { ParsedResumeData } from "./types";

function asParsed(data: unknown): ParsedResumeData | null {
  if (!data || typeof data !== "object") return null;
  return data as ParsedResumeData;
}

export function ParsedResumePanel({ data }: { data: unknown }) {
  const parsed = asParsed(data);
  if (!parsed) return null;

  const skills = parsed.skills ?? [];
  const experience = parsed.experience ?? [];
  const education = parsed.education ?? [];
  const projects = parsed.projects ?? [];
  const certifications = parsed.certifications ?? [];

  return (
    <div className="parsed-panel stack">
      <div>
        <h3 className="parsed-heading">AI-parsed profile</h3>
        <p className="muted" style={{ margin: 0 }}>
          Structured signal extracted for fit scoring.
        </p>
      </div>

      {(parsed.full_name || parsed.seniority || parsed.total_years_experience != null) && (
        <div className="parsed-meta row">
          {parsed.full_name ? <span className="status-pill ok">{parsed.full_name}</span> : null}
          {parsed.seniority ? (
            <span className="status-pill warn">Seniority: {parsed.seniority}</span>
          ) : null}
          {parsed.total_years_experience != null ? (
            <span className="status-pill warn">
              {parsed.total_years_experience} yrs experience
            </span>
          ) : null}
        </div>
      )}

      {parsed.summary ? (
        <div>
          <h4 className="parsed-sub">Summary</h4>
          <p className="parsed-text">{parsed.summary}</p>
        </div>
      ) : null}

      {skills.length > 0 ? (
        <div>
          <h4 className="parsed-sub">Skills</h4>
          <div className="chip-row">
            {skills.map((skill) => (
              <span className="chip" key={skill}>
                {skill}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {experience.length > 0 ? (
        <div>
          <h4 className="parsed-sub">Experience</h4>
          <div className="stack" style={{ gap: "0.75rem" }}>
            {experience.map((job, idx) => (
              <div className="parsed-block" key={`${job.company}-${job.title}-${idx}`}>
                <strong>
                  {job.title || "Role"}
                  {job.company ? ` · ${job.company}` : ""}
                </strong>
                {(job.start_date || job.end_date) && (
                  <p className="muted parsed-dates">
                    {[job.start_date, job.end_date || "Present"].filter(Boolean).join(" → ")}
                  </p>
                )}
                {job.description ? <p className="parsed-text">{job.description}</p> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {education.length > 0 ? (
        <div>
          <h4 className="parsed-sub">Education</h4>
          <div className="stack" style={{ gap: "0.55rem" }}>
            {education.map((edu, idx) => (
              <div className="parsed-block" key={`${edu.institution}-${idx}`}>
                <strong>
                  {[edu.degree, edu.field_of_study].filter(Boolean).join(" · ") || "Education"}
                </strong>
                <p className="muted parsed-dates">
                  {[edu.institution, edu.end_date].filter(Boolean).join(" · ")}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {projects.length > 0 ? (
        <div>
          <h4 className="parsed-sub">Projects</h4>
          <div className="stack" style={{ gap: "0.55rem" }}>
            {projects.map((project, idx) => (
              <div className="parsed-block" key={`${project.name}-${idx}`}>
                <strong>{project.name || "Project"}</strong>
                {project.description ? <p className="parsed-text">{project.description}</p> : null}
                {project.technologies && project.technologies.length > 0 ? (
                  <div className="chip-row" style={{ marginTop: "0.35rem" }}>
                    {project.technologies.map((tech) => (
                      <span className="chip" key={tech}>
                        {tech}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {certifications.length > 0 ? (
        <div>
          <h4 className="parsed-sub">Certifications</h4>
          <ul className="parsed-list">
            {certifications.map((cert, idx) => {
              const label =
                typeof cert === "string"
                  ? cert
                  : [cert.name, cert.issuer, cert.date_issued].filter(Boolean).join(" · ");
              return <li key={`${label}-${idx}`}>{label}</li>;
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
