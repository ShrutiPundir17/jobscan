import { LiquidMetal, liquidMetalPresets } from "@paper-design/shaders-react";
import { motion, useReducedMotion } from "framer-motion";
import type { CSSProperties, ReactNode } from "react";

export type LiquidMetalHeroAction = {
  label: string;
  onClick?: () => void;
};

export type LiquidMetalHeroProps = {
  headline: ReactNode;
  subtitle: ReactNode;
  primaryAction: LiquidMetalHeroAction;
  secondaryAction: LiquidMetalHeroAction;
  marqueeText?: string;
  /** Content alignment — use "start" for editorial left-aligned headlines. */
  align?: "center" | "start";
};

const easeOut = [0.16, 1, 0.3, 1] as const;

function MarqueeStrip({
  text,
  direction,
  duration,
}: {
  text: string;
  direction: "left" | "right";
  duration: number;
}) {
  const unit = `${text} · `;
  const track = unit.repeat(24);
  return (
    <div className="lmh-marquee" aria-hidden>
      <div
        className={`lmh-marquee-track lmh-marquee-${direction}`}
        style={{ animationDuration: `${duration}s` }}
      >
        {track}
        {track}
      </div>
    </div>
  );
}

export function LiquidMetalHero({
  headline,
  subtitle,
  primaryAction,
  secondaryAction,
  marqueeText,
  align = "center",
}: LiquidMetalHeroProps) {
  const reduceMotion = useReducedMotion();
  const preset = (liquidMetalPresets[2]?.params ?? {}) as Record<string, unknown>;

  const breatheStyle: CSSProperties | undefined = reduceMotion
    ? undefined
    : { animation: "lmh-breathe 8s ease-in-out infinite" };
  const spinStyle: CSSProperties | undefined = reduceMotion
    ? undefined
    : { animation: "lmh-spin 20s linear infinite" };

  return (
    <section className={`lmh ${align === "start" ? "lmh-align-start" : ""}`}>
      {marqueeText ? (
        <>
          <div className="lmh-strip lmh-strip-top">
            <MarqueeStrip text={marqueeText} direction="left" duration={48} />
          </div>
          <div className="lmh-strip lmh-strip-bottom">
            <MarqueeStrip text={marqueeText} direction="right" duration={64} />
          </div>
        </>
      ) : null}

      <div className="lmh-orb-wrap" aria-hidden>
        <div className="lmh-orb" style={breatheStyle}>
          <div className="lmh-orb-spin" style={spinStyle}>
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
              speed={reduceMotion ? 0 : 0.35}
              scale={1.15}
              fit="contain"
              style={{ width: "100%", height: "100%" }}
            />
          </div>
          <div className="lmh-orb-light" />
        </div>
      </div>

      <div className="lmh-scrim" aria-hidden />

      <div className="lmh-content">
        <motion.h1
          className="lmh-headline"
          initial={reduceMotion ? false : { opacity: 0, y: 28 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: easeOut }}
        >
          {headline}
        </motion.h1>

        <motion.p
          className="lmh-subtitle"
          initial={reduceMotion ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: easeOut, delay: 0.2 }}
        >
          {subtitle}
        </motion.p>

        <motion.div
          className="lmh-actions"
          initial={reduceMotion ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: easeOut, delay: 0.4 }}
        >
          <button type="button" className="lmh-btn lmh-btn-primary" onClick={primaryAction.onClick}>
            {primaryAction.label}
          </button>
          <button
            type="button"
            className="lmh-btn lmh-btn-secondary"
            onClick={secondaryAction.onClick}
          >
            {secondaryAction.label}
          </button>
        </motion.div>
      </div>
    </section>
  );
}
