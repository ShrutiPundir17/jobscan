import { useEffect, useState } from "react";

function toneColor(score: number): string {
  if (score >= 80) return "#10B981";
  if (score >= 60) return "#F59E0B";
  return "#F43F5E";
}

type Props = {
  score: number | null;
  size?: number;
};

export function ScoreGauge({ score, size = 96 }: Props) {
  const target = score ?? 0;
  const [display, setDisplay] = useState(0);
  const r = 36;
  const circ = 2 * Math.PI * r;
  const offset = circ - (Math.min(100, Math.max(0, display)) / 100) * circ;
  const color = toneColor(target);

  useEffect(() => {
    if (score == null) {
      setDisplay(0);
      return;
    }
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setDisplay(score);
      return;
    }
    setDisplay(0);
    const start = performance.now();
    const duration = 800;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      setDisplay(Math.round(score * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [score]);

  return (
    <div
      className="score-gauge"
      style={{ width: size, height: size, ["--gauge-circ" as string]: String(circ) }}
    >
      <svg viewBox="0 0 88 88" aria-label={`Match score ${score ?? "—"}`}>
        <defs>
          <linearGradient id={`gaugeGrad-${color}`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={color} />
            <stop offset="100%" stopColor={score != null && score >= 80 ? "#34D399" : color} />
          </linearGradient>
        </defs>
        <circle className="track" cx="44" cy="44" r={r} />
        <circle
          className="fill"
          cx="44"
          cy="44"
          r={r}
          stroke={`url(#gaugeGrad-${color})`}
          strokeDasharray={circ}
          strokeDashoffset={score == null ? circ : offset}
          style={{ transition: "stroke-dashoffset 800ms ease-in-out" }}
        />
        <text className="label" x="44" y="50" textAnchor="middle">
          {score == null ? "—" : display}
        </text>
      </svg>
    </div>
  );
}
