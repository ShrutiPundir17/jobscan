import { useEffect, useState } from "react";
import { ScoreGauge } from "./components/ScoreGauge";

type Props = {
  onLogin: () => void;
  onSignup: () => void;
};

function LiveScoreDemo() {
  const [score, setScore] = useState(0);
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setScore(91);
      setPhase(3);
      return;
    }
    setScore(0);
    setPhase(0);
    const t1 = window.setTimeout(() => setScore(91), 200);
    const t2 = window.setTimeout(() => setPhase(1), 900);
    const t3 = window.setTimeout(() => setPhase(2), 1200);
    const t4 = window.setTimeout(() => setPhase(3), 1600);
    const loop = window.setInterval(() => {
      setScore(0);
      setPhase(0);
      window.setTimeout(() => setScore(91), 200);
      window.setTimeout(() => setPhase(1), 900);
      window.setTimeout(() => setPhase(2), 1200);
      window.setTimeout(() => setPhase(3), 1600);
    }, 8000);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      clearTimeout(t4);
      clearInterval(loop);
    };
  }, []);

  return (
    <div className="stack" style={{ gap: "0.75rem" }}>
      <div className="row space-between" style={{ alignItems: "flex-start" }}>
        <div>
          <div style={{ fontWeight: 600 }}>Backend Engineer</div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            Atlassian · Bangalore
          </div>
        </div>
        <ScoreGauge score={score || null} size={88} />
      </div>
      {phase >= 1 ? (
        <ul className="why-list">
          <li>Production APIs & system design</li>
          <li>Strong TypeScript / React fit</li>
        </ul>
      ) : null}
      {phase >= 2 ? (
        <span className="verdict-pill strong">Strong Fit</span>
      ) : null}
      {phase >= 3 ? (
        <div className="live-ticker">Applied via One-tap</div>
      ) : (
        <p className="muted" style={{ fontSize: "0.78rem" }}>
          Scoring in real time…
        </p>
      )}
    </div>
  );
}

export function LandingPage({ onLogin, onSignup }: Props) {
  return (
    <div className="landing">
      <div className="orb orb-a" aria-hidden />
      <div className="orb orb-b" aria-hidden />
      <nav className="landing-nav">
        <p className="brand">
          Job<span>Agent</span>
        </p>
        <div className="landing-nav-links">
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#pricing">Pricing</a>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onLogin}>
            Login
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={onSignup}>
            Sign Up Free
          </button>
        </div>
      </nav>

      <section className="landing-hero fade-in">
        <div style={{ position: "relative", zIndex: 1 }}>
          <p className="brand" style={{ fontSize: "1.25rem" }}>
            Job<span>Agent</span>
          </p>
          <h1>
            Your <span className="grad-text">AI job agent</span>. Always hunting. Always
            applying.
          </h1>
          <p className="lede">
            Upload a resume. We find fits, score them, and apply when you say go — calm
            confidence for your search.
          </p>
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={onSignup}>
              Get started — free
            </button>
            <a className="btn btn-secondary" href="#how">
              See how it works
            </a>
          </div>
        </div>
        <div className="hero-demo" aria-label="Product preview" style={{ zIndex: 1 }}>
          <div className="hero-demo-label">Live scoring</div>
          <LiveScoreDemo />
        </div>
      </section>

      <section className="landing-section" id="how">
        <p className="section-kicker">How it works</p>
        <div className="how-grid">
          {[
            ["1", "Upload resume", "AI parses skills, experience, and seniority in seconds."],
            ["2", "Set preferences", "Roles, cities, score threshold — your agent listens."],
            ["3", "Hunt & apply", "Matches scored daily. Apply in one tap when ready."],
          ].map(([n, t, d]) => (
            <div className="how-card" key={n}>
              <div className="how-icon">{n}</div>
              <h3 style={{ fontSize: "1rem", marginBottom: 6 }}>{t}</h3>
              <p className="muted" style={{ fontSize: "0.875rem" }}>
                {d}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section" id="features">
        <p className="section-kicker">Features</p>
        <div className="features-grid">
          {[
            ["AI", "AI matching", "Vector search + Gemini deep-score against every JD."],
            ["AA", "Auto-apply", "Apply above your threshold — you stay in control."],
            ["AL", "Real-time alerts", "Email & WhatsApp when a strong fit lands."],
            ["TL", "Resume tailor", "JD-specific bullets and pitch, one click."],
            ["MP", "Multi-portal scan", "LinkedIn, Internshala, Naukri, Foundit, Unstop."],
            ["KB", "Application tracker", "Kanban from Applied through Offer."],
          ].map(([icon, t, d], i) => (
            <div className="feature-card" key={`${t}-${i}`}>
              <div className="feature-glow-icon">{icon}</div>
              <h3>{t}</h3>
              <p>{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section" id="pricing">
        <p className="section-kicker">Pricing</p>
        <div className="pricing-grid">
          <div className="price-card">
            <h3>Free</h3>
            <div className="price-amount">₹0</div>
            <ul>
              <li>3 scans / week</li>
              <li>5 match scores / day</li>
              <li>Manual apply</li>
              <li>Email alerts</li>
            </ul>
            <button type="button" className="btn btn-secondary btn-block" onClick={onSignup}>
              Start free
            </button>
          </div>
          <div className="price-card featured">
            <span className="price-badge">Most popular</span>
            <h3>Pro</h3>
            <div className="price-amount">₹299/mo</div>
            <ul>
              <li>Unlimited scans</li>
              <li>Priority scoring</li>
              <li>One-tap apply + WhatsApp</li>
              <li>Auto-apply above threshold</li>
            </ul>
            <button type="button" className="btn btn-primary btn-block" onClick={onSignup}>
              Go Pro
            </button>
          </div>
          <div className="price-card">
            <h3>Premium</h3>
            <div className="price-amount">₹799/mo</div>
            <ul>
              <li>Everything in Pro</li>
              <li>Tailored resume variants</li>
              <li>Interview reminders</li>
              <li>Priority support</li>
            </ul>
            <button type="button" className="btn btn-secondary btn-block" onClick={onSignup}>
              Go Premium
            </button>
          </div>
        </div>
      </section>

      <footer className="landing-footer">
        <div>
          <h4>Product</h4>
          <a href="#features">Features</a>
          <a href="#pricing">Pricing</a>
        </div>
        <div>
          <h4>Company</h4>
          <a href="#how">About</a>
          <span className="dim" style={{ fontSize: "0.85rem" }}>
            Careers
          </span>
        </div>
        <div>
          <h4>Legal</h4>
          <span className="dim" style={{ fontSize: "0.85rem" }}>
            Privacy
          </span>
          <br />
          <span className="dim" style={{ fontSize: "0.85rem" }}>
            Terms
          </span>
        </div>
        <div>
          <h4>Social</h4>
          <span className="dim" style={{ fontSize: "0.85rem" }}>
            Twitter
          </span>
          <br />
          <span className="dim" style={{ fontSize: "0.85rem" }}>
            LinkedIn
          </span>
        </div>
      </footer>
    </div>
  );
}
