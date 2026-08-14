import { LiquidMetal, liquidMetalPresets } from "@paper-design/shaders-react";
import { useReducedMotion } from "framer-motion";
import type { CSSProperties } from "react";

/** Fixed viewport liquid-metal backdrop — stays put while the page scrolls. */
export function LiquidMetalBackdrop() {
  const reduceMotion = useReducedMotion();
  const preset = (liquidMetalPresets[2]?.params ?? {}) as Record<string, unknown>;

  const breatheStyle: CSSProperties | undefined = reduceMotion
    ? undefined
    : { animation: "lmh-breathe 8s ease-in-out infinite" };
  const spinStyle: CSSProperties | undefined = reduceMotion
    ? undefined
    : { animation: "lmh-spin 20s linear infinite" };

  return (
    <div className="lp-fixed-blob" aria-hidden>
      <div className="lp-fixed-blob-orb" style={breatheStyle}>
        <div className="lp-fixed-blob-spin" style={spinStyle}>
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
            scale={1.2}
            fit="contain"
            style={{ width: "100%", height: "100%", opacity: 0.9 }}
          />
        </div>
        <div className="lp-fixed-blob-light" />
      </div>
    </div>
  );
}
