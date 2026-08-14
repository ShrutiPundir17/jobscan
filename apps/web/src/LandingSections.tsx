import { LiquidMetal, liquidMetalPresets } from "@paper-design/shaders-react";
import {
  motion,
  useInView,
  useMotionValue,
  useReducedMotion,
  useSpring,
} from "framer-motion";
import { useEffect, useRef, type ReactNode } from "react";

const easeOut = [0.16, 1, 0.3, 1] as const;
const viewport = { once: true, margin: "-100px" as const };

function IconDoc() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-6Z"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path d="M14 2v6h6M9 13h6M9 17h6" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function IconRadar() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.5" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function IconSend() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FadeIn({
  children,
  className,
  delay = 0,
  x = 0,
  y = 24,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
  x?: number;
  y?: number;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? false : { opacity: 0, x, y }}
      whileInView={{ opacity: 1, x: 0, y: 0 }}
      viewport={viewport}
      transition={{ duration: 0.55, ease: easeOut, delay }}
    >
      {children}
    </motion.div>
  );
}

function useCountUp(target: number, enabled: boolean, duration = 1.4) {
  const motionVal = useMotionValue(0);
  const spring = useSpring(motionVal, { stiffness: 60, damping: 20 });
  const ref = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    motionVal.set(0);
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / (duration * 1000));
      const eased = 1 - (1 - t) ** 3;
      ref.current = target * eased;
      motionVal.set(ref.current);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [enabled, target, duration, motionVal]);

  return spring;
}

function StatItem({
  numeric,
  suffix,
  prefix = "",
  label,
  decimals = 0,
}: {
  numeric: number;
  suffix: string;
  prefix?: string;
  label: string;
  decimals?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, viewport);
  const value = useCountUp(numeric, inView);
  const reduce = useReducedMotion();
  const displayRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (reduce && displayRef.current) {
      displayRef.current.textContent = `${prefix}${
        decimals ? numeric.toFixed(decimals) : Math.round(numeric)
      }${suffix}`;
      return;
    }
    const unsub = value.on("change", (v) => {
      if (!displayRef.current) return;
      displayRef.current.textContent = `${prefix}${
        decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString("en-IN")
      }${suffix}`;
    });
    return unsub;
  }, [value, prefix, suffix, decimals, numeric, reduce]);

  return (
    <div className="lp-stat" ref={ref}>
      <span className="lp-stat-value" ref={displayRef}>
        {prefix}
        {decimals ? numeric.toFixed(decimals) : numeric}
        {suffix}
      </span>
      <span className="lp-stat-label">{label}</span>
    </div>
  );
}

const STEPS = [
  {
    n: "01",
    title: "Upload your resume",
    desc: "Drop your PDF or DOCX once. Our AI reads it, understands it, and never asks again.",
    icon: <IconDoc />,
  },
  {
    n: "02",
    title: "We hunt 24/7",
    desc: "JobAgent scrapes Naukri, LinkedIn, Internshala and more every 2 hours. You sleep. We search.",
    icon: <IconRadar />,
  },
  {
    n: "03",
    title: "Apply on your terms",
    desc: "Get notified when a strong match is found. Review it and apply in one tap — or let us apply automatically.",
    icon: <IconSend />,
  },
] as const;

const PORTALS = [
  "Naukri",
  "LinkedIn",
  "Internshala",
  "Foundit",
  "Unstop",
  "Wellfound",
  "Direct Company Pages",
];

type Props = {
  onLogin: () => void;
  onSignup: () => void;
};

export function LandingSections({ onSignup }: Props) {
  const reduce = useReducedMotion();
  const preset = (liquidMetalPresets[2]?.params ?? {}) as Record<string, unknown>;

  function scrollTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <>
      {/* Section 1 — Stats */}
      <section className="lp-stats" aria-label="Product stats">
        <FadeIn className="lp-stats-inner">
          <StatItem numeric={2} suffix=" hrs" label="How often we scan portals" />
          <div className="lp-stats-divider" aria-hidden />
          <StatItem numeric={87} suffix="%" label="Average match accuracy" />
          <div className="lp-stats-divider" aria-hidden />
          <StatItem numeric={10} suffix="x" label="Faster than manual applying" />
          <div className="lp-stats-divider" aria-hidden />
          <StatItem numeric={300} suffix="+" label="Job portals monitored" />
        </FadeIn>
      </section>

      {/* Section 2 — How it works */}
      <section className="lp-section" id="how">
        <div className="lp-wrap">
          <p className="lp-eyebrow">How it works</p>
          <div className="lp-steps">
            {STEPS.map((step, i) => (
              <FadeIn key={step.n} className="lp-step" delay={i * 0.1} y={32}>
                <span className="lp-step-num" aria-hidden>
                  {step.n}
                </span>
                <div className="lp-step-icon">{step.icon}</div>
                <h3 className="lp-step-title">{step.title}</h3>
                <p className="lp-step-desc">{step.desc}</p>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* Section 3 — Features */}
      <section className="lp-section" id="features">
        <div className="lp-wrap lp-features">
          <FadeIn className="lp-feature-row" x={-40}>
            <div className="lp-feature-copy">
              <h2 className="lp-feature-title">AI that actually reads the JD</h2>
              <p className="lp-feature-body">
                Not keyword matching. GPT-4o reads the job description and your resume like a
                recruiter would. You get a score, match reasons, and exact gaps — not just a
                percentage.
              </p>
            </div>
            <div className="lp-glass lp-match-card">
              <div className="lp-gauge">
                <span className="lp-gauge-score">87</span>
                <span className="lp-gauge-max">/100</span>
              </div>
              <p className="lp-match-label">Strong match</p>
              <ul className="lp-match-list">
                <li className="ok">Python + FastAPI experience</li>
                <li className="ok">Bangalore location fit</li>
                <li className="ok">2+ years relevant work</li>
                <li className="gap">Kubernetes production ops</li>
                <li className="gap">System design portfolio</li>
              </ul>
            </div>
          </FadeIn>

          <FadeIn className="lp-feature-row lp-feature-reverse" x={40}>
            <div className="lp-feature-copy">
              <h2 className="lp-feature-title">Your resume, tailored per job</h2>
              <p className="lp-feature-body">
                Before applying, we rewrite your resume bullets to match the specific JD — same
                facts, better framing. Automatically.
              </p>
            </div>
            <div className="lp-glass lp-tailor-card">
              <div className="lp-tailor-col">
                <span className="lp-pill dim">Before</span>
                <p>Worked on backend APIs and databases for internal tools.</p>
              </div>
              <div className="lp-tailor-arrow" aria-hidden>
                →
              </div>
              <div className="lp-tailor-col">
                <span className="lp-pill">After</span>
                <p>
                  Built FastAPI services and Postgres pipelines powering high-volume job matching.
                </p>
              </div>
            </div>
          </FadeIn>

          <FadeIn className="lp-feature-row" x={-40}>
            <div className="lp-feature-copy">
              <h2 className="lp-feature-title">Track everything in one place</h2>
              <p className="lp-feature-body">
                Applied, viewed, phone screen, interview, offer — every application tracked. Never
                lose track of where you stand.
              </p>
            </div>
            <div className="lp-glass lp-kanban-card">
              {["Applied", "Interview", "Offer"].map((col) => (
                <div className="lp-kanban-col" key={col}>
                  <span>{col}</span>
                  <div className="lp-kanban-item" />
                  {col === "Applied" ? <div className="lp-kanban-item" /> : null}
                </div>
              ))}
            </div>
          </FadeIn>
        </div>
      </section>

      {/* Section 4 — Portals marquee */}
      <section className="lp-section lp-portals" aria-label="Supported portals">
        <p className="lp-eyebrow center">We monitor these portals</p>
        <div className="lp-portal-marquee" onMouseEnter={(e) => e.currentTarget.classList.add("paused")} onMouseLeave={(e) => e.currentTarget.classList.remove("paused")}>
          <div className="lp-portal-track">
            {[...PORTALS, ...PORTALS, ...PORTALS].map((name, i) => (
              <span className="lp-portal-chip" key={`${name}-${i}`}>
                {name}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* Section 5 — Pricing */}
      <section className="lp-section" id="pricing">
        <div className="lp-wrap">
          <p className="lp-eyebrow center">Pricing</p>
          <h2 className="lp-section-title center">Simple plans. Serious hunting.</h2>
          <div className="lp-pricing">
            {[
              {
                name: "Free",
                price: "₹0",
                period: "/month",
                popular: false,
                cta: "Get started free",
                features: ["10 job alerts/week", "1 portal", "Notify only", "Basic matching"],
              },
              {
                name: "Pro",
                price: "₹299",
                period: "/month",
                popular: true,
                cta: "Start Pro",
                features: [
                  "Unlimited alerts",
                  "5 portals",
                  "Resume tailoring",
                  "20 auto-applies/week",
                  "Email notifications",
                ],
              },
              {
                name: "Premium",
                price: "₹799",
                period: "/month",
                popular: false,
                cta: "Go Premium",
                features: [
                  "All portals",
                  "Unlimited auto-apply",
                  "WhatsApp alerts",
                  "Priority matching",
                  "Interview prep",
                ],
              },
            ].map((plan, i) => (
              <FadeIn
                key={plan.name}
                className={`lp-price-card ${plan.popular ? "popular" : ""}`}
                delay={i * 0.1}
                y={36}
              >
                {plan.popular ? <span className="lp-popular-badge">Most Popular</span> : null}
                <h3>{plan.name}</h3>
                <div className="lp-price">
                  <span className="lp-price-num">{plan.price}</span>
                  <span className="lp-price-period">{plan.period}</span>
                </div>
                <ul>
                  {plan.features.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
                <button
                  type="button"
                  className={`lp-btn ${plan.popular ? "lp-btn-glow" : "lp-btn-ghost"}`}
                  onClick={onSignup}
                >
                  {plan.cta}
                </button>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* Section 6 — Final CTA */}
      <section className="lp-final" id="cta">
        <div className="lp-final-blob" aria-hidden>
          <LiquidMetal
            {...preset}
            shape="metaballs"
            colorBack="#000000"
            colorTint="#C0C0C0"
            shiftRed={0.22}
            shiftBlue={0.28}
            softness={0.35}
            distortion={0.12}
            contour={0.45}
            speed={reduce ? 0 : 0.2}
            scale={1.2}
            fit="contain"
            style={{ width: "100%", height: "100%", opacity: 0.15 }}
          />
        </div>
        <FadeIn className="lp-final-inner" y={20}>
          <h2 className="lp-final-title">Stop applying manually.</h2>
          <p className="lp-final-sub">Let your agent do it.</p>
          <div className="lp-final-actions">
            <button type="button" className="lp-btn lp-btn-solid" onClick={onSignup}>
              Get started free
            </button>
            <button type="button" className="lp-btn lp-btn-ghost" onClick={() => scrollTo("how")}>
              See how it works
            </button>
          </div>
        </FadeIn>
      </section>
    </>
  );
}

export function LandingNav({ onLogin, onSignup }: Props) {
  function go(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  return (
    <header className="lp-nav">
      <a className="lp-nav-brand" href="#top" onClick={(e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
        Job<span>Agent</span>
      </a>
      <nav className="lp-nav-links" aria-label="Primary">
        <button type="button" onClick={() => go("how")}>
          How it works
        </button>
        <button type="button" onClick={() => go("features")}>
          Features
        </button>
        <button type="button" onClick={() => go("pricing")}>
          Pricing
        </button>
      </nav>
      <div className="lp-nav-actions">
        <button type="button" className="lp-nav-login" onClick={onLogin}>
          Log in
        </button>
        <button type="button" className="lp-btn lp-btn-solid lp-btn-sm" onClick={onSignup}>
          Get Started
        </button>
      </div>
    </header>
  );
}

export function LandingFooter({ onSignup }: { onSignup: () => void }) {
  return (
    <footer className="lp-footer">
      <div className="lp-wrap lp-footer-grid">
        <div>
          <p className="lp-nav-brand">
            Job<span>Agent</span>
          </p>
          <p className="lp-footer-tag">Your AI job agent. Always hunting.</p>
        </div>
        <div>
          <h4>Product</h4>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#pricing">Pricing</a>
          <button type="button" onClick={onSignup}>
            Get started
          </button>
        </div>
        <div>
          <h4>Company</h4>
          <a href="#cta">About</a>
          <a href="#cta">Contact</a>
          <a href="#cta">Privacy</a>
          <a href="#cta">Terms</a>
        </div>
        <div>
          <h4>Social</h4>
          <div className="lp-social">
            <a href="https://linkedin.com" target="_blank" rel="noreferrer" aria-label="LinkedIn">
              in
            </a>
            <a href="https://x.com" target="_blank" rel="noreferrer" aria-label="X">
              𝕏
            </a>
            <a href="https://github.com" target="_blank" rel="noreferrer" aria-label="GitHub">
              gh
            </a>
          </div>
        </div>
      </div>
      <div className="lp-footer-bottom">
        <span>© {new Date().getFullYear()} JobAgent</span>
      </div>
    </footer>
  );
}
