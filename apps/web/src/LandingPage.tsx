import { LiquidMetalHero } from "./components/LiquidMetalHero";

type Props = {
  onLogin: () => void;
  onSignup: () => void;
};

export function LandingPage({ onLogin, onSignup }: Props) {
  return (
    <LiquidMetalHero
      align="start"
      headline={
        <>
          Stop refreshing <span className="lmh-mark-green">Naukri.</span>
          <br />
          Your agent is <span className="lmh-strike">looking</span>{" "}
          <span className="lmh-headline-tail">
            already
            <br />
            applying.
          </span>
        </>
      }
      subtitle="Upload a resume. We find fits, score them, and apply when you say go."
      primaryAction={{ label: "Get started", onClick: onSignup }}
      secondaryAction={{ label: "Log in", onClick: onLogin }}
      marqueeText="JOBAGENT · HUNT · MATCH · APPLY"
    />
  );
}
