# LiquidMetalHero

Reusable full-viewport hero: deep black + liquid metal orb + centered type + two minimal CTAs.

**Location:** `design/liquid-metal-hero/` (standalone drop-in — not imported by the Vite JobAgent app).

Copy `LiquidMetalHero.tsx` into your Next.js 14 App Router project (e.g. `components/LiquidMetalHero.tsx`).

## Install (Next.js)

```bash
npm i framer-motion @paper-design/shaders-react
npx shadcn@latest add button
```

Ensure Tailwind is configured and `@/` maps to your `src/` (or `app/` sibling) root.

## Usage

```tsx
import { LiquidMetalHero } from "@/components/LiquidMetalHero";

export default function Page() {
  return (
    <LiquidMetalHero
      headline={<>Your headline</>}
      subtitle={<>Your supporting line</>}
      primaryAction={{ label: "Primary", href: "/start" }}
      secondaryAction={{ label: "Secondary", href: "/more" }}
      marqueeText="optional strip texture"
    />
  );
}
```

## Motion

| Layer | Spec |
|-------|------|
| Blob breathe | 8s ease-in-out scale 1 → 1.045 |
| Blob spin | 20s linear |
| Shader flow | `speed={0.35}` on `LiquidMetal` |
| Headline | fade + slide up 400ms |
| Subtitle | +200ms delay |
| Buttons | +400ms after subtitle |
| Marquee | left 48s / right 64s (parallax) |

`prefers-reduced-motion` freezes CSS loops and Framer enters at rest.
