"use client";

/**
 * LiquidMetalHero — visual-only hero shell (mercury / fluid glass).
 *
 * Next.js 14 App Router drop-in. Requires:
 *   npm i framer-motion @paper-design/shaders-react
 *   Tailwind CSS + shadcn/ui Button (`@/components/ui/button`)
 *
 * Pass all copy via props — no marketing text is baked in.
 */

import { LiquidMetal, liquidMetalPresets } from "@paper-design/shaders-react";
import { motion, useReducedMotion } from "framer-motion";
import type { CSSProperties, ReactNode } from "react";
import { Button } from "@/components/ui/button";

export type LiquidMetalHeroAction = {
  label: string;
  href?: string;
  onClick?: () => void;
};

export type LiquidMetalHeroProps = {
  headline: ReactNode;
  subtitle: ReactNode;
  primaryAction: LiquidMetalHeroAction;
  secondaryAction: LiquidMetalHeroAction;
  /** Repeated on faint edge strips for depth. Omit to hide strips. */
  marqueeText?: string;
  className?: string;
};

/** Design tokens for this hero (reference when theming adjacent surfaces). */
export const liquidMetalHeroTokens = {
  background: "#000000",
  textPrimary: "#FFFFFF",
  textSecondary: "#A0A0A0",
  strip: "#333333",
  blobBase: "#C0C0C0",
  goldHighlight: "rgba(212,175,55,0.18)",
  blueReflection: "rgba(96,165,250,0.16)",
  buttonPrimaryBg: "#FFFFFF",
  buttonPrimaryFg: "#000000",
  buttonSecondaryBg: "#000000",
  buttonSecondaryFg: "#FFFFFF",
} as const;

const easeOut = [0.16, 1, 0.3, 1] as const;

const heroKeyframes = `
@keyframes lmh-marquee-left {
  from { transform: translateX(0); }
  to { transform: translateX(-50%); }
}
@keyframes lmh-marquee-right {
  from { transform: translateX(-50%); }
  to { transform: translateX(0); }
}
@keyframes lmh-breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.045); }
}
@keyframes lmh-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .lmh-anim-breathe,
  .lmh-anim-spin,
  .lmh-anim-marquee {
    animation: none !important;
  }
}
`;

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
    <div
      className="pointer-events-none absolute left-0 right-0 overflow-hidden whitespace-nowrap"
      aria-hidden
    >
      <div
        className="lmh-anim-marquee inline-block text-[10px] font-medium uppercase tracking-[0.35em] text-[#333333]"
        style={{
          animation: `${
            direction === "left" ? "lmh-marquee-left" : "lmh-marquee-right"
          } ${duration}s linear infinite`,
        }}
      >
        {track}
        {track}
      </div>
    </div>
  );
}

function ActionButton({
  action,
  variant,
}: {
  action: LiquidMetalHeroAction;
  variant: "primary" | "secondary";
}) {
  const className =
    variant === "primary"
      ? [
          "h-auto rounded-full border-0 bg-white px-8 py-4 text-base font-semibold text-black",
          "shadow-none hover:bg-white hover:scale-[1.02] active:scale-[0.98]",
          "transition-transform duration-150 ease-out",
        ].join(" ")
      : [
          "h-auto rounded-full border border-white bg-black px-8 py-4 text-base font-semibold text-white",
          "shadow-none hover:bg-black hover:scale-[1.02] active:scale-[0.98]",
          "transition-transform duration-150 ease-out",
        ].join(" ");

  if (action.href) {
    return (
      <Button asChild variant="ghost" className={className}>
        <a href={action.href} onClick={action.onClick}>
          {action.label}
        </a>
      </Button>
    );
  }

  return (
    <Button type="button" variant="ghost" className={className} onClick={action.onClick}>
      {action.label}
    </Button>
  );
}

/**
 * Full-viewport centered hero: black void + liquid metal orb + type + two CTAs.
 */
export function LiquidMetalHero({
  headline,
  subtitle,
  primaryAction,
  secondaryAction,
  marqueeText,
  className = "",
}: LiquidMetalHeroProps) {
  const reduceMotion = useReducedMotion();

  // Preset [2] = Backdrop chrome params; shape forced to metaballs for a centered orb.
  const preset = (liquidMetalPresets[2]?.params ?? {}) as Record<string, unknown>;

  const breatheStyle: CSSProperties | undefined = reduceMotion
    ? undefined
    : { animation: "lmh-breathe 8s ease-in-out infinite" };

  const spinStyle: CSSProperties | undefined = reduceMotion
    ? undefined
    : { animation: "lmh-spin 20s linear infinite" };

  return (
    <section
      className={[
        "relative flex min-h-screen w-full items-center justify-center overflow-hidden bg-black",
        "px-6 py-24",
        className,
      ].join(" ")}
    >
      <style dangerouslySetInnerHTML={{ __html: heroKeyframes }} />

      {marqueeText ? (
        <>
          <div className="absolute top-6 w-full">
            <MarqueeStrip text={marqueeText} direction="left" duration={48} />
          </div>
          <div className="absolute bottom-6 w-full">
            <MarqueeStrip text={marqueeText} direction="right" duration={64} />
          </div>
        </>
      ) : null}

      <div
        className="pointer-events-none absolute inset-0 z-0 flex items-center justify-center"
        aria-hidden
      >
        <div
          className="lmh-anim-breathe relative h-[min(72vw,640px)] w-[min(72vw,640px)] opacity-90"
          style={breatheStyle}
        >
          <div className="lmh-anim-spin absolute inset-[-8%] h-[116%] w-[116%]" style={spinStyle}>
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
          <div
            className="pointer-events-none absolute inset-0 rounded-full"
            style={{
              background: `radial-gradient(circle at 32% 28%, ${liquidMetalHeroTokens.goldHighlight}, transparent 42%), radial-gradient(circle at 70% 65%, ${liquidMetalHeroTokens.blueReflection}, transparent 45%)`,
              mixBlendMode: "screen",
            }}
          />
        </div>
      </div>

      <div
        className="pointer-events-none absolute inset-0 z-[1]"
        aria-hidden
        style={{
          background:
            "radial-gradient(ellipse 55% 45% at 50% 50%, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.15) 55%, transparent 75%)",
        }}
      />

      <div className="relative z-10 mx-auto flex max-w-[900px] flex-col items-center text-center">
        <motion.h1
          className="m-0 max-w-[18ch] text-[clamp(3.5rem,10vw,6.25rem)] font-extrabold leading-[0.95] tracking-[-0.04em] text-white"
          initial={reduceMotion ? false : { opacity: 0, y: 28 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: easeOut }}
        >
          {headline}
        </motion.h1>

        <motion.div
          className="mt-6 max-w-[700px] text-[20px] leading-relaxed text-[#A0A0A0] sm:text-[22px]"
          initial={reduceMotion ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: easeOut, delay: 0.2 }}
        >
          {subtitle}
        </motion.div>

        <motion.div
          className="mt-10 flex flex-wrap items-center justify-center gap-4"
          initial={reduceMotion ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: easeOut, delay: 0.4 }}
        >
          <ActionButton action={primaryAction} variant="primary" />
          <ActionButton action={secondaryAction} variant="secondary" />
        </motion.div>
      </div>
    </section>
  );
}

export default LiquidMetalHero;
