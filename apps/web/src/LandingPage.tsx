import { LiquidMetalBackdrop } from "./components/LiquidMetalBackdrop";
import { LiquidMetalHero } from "./components/LiquidMetalHero";
import { LandingFooter, LandingNav, LandingSections } from "./LandingSections";

type Props = {
  onLogin: () => void;
  onSignup: () => void;
};

export function LandingPage({ onLogin, onSignup }: Props) {
  return (
    <div className="lp-page" id="top">
      <LiquidMetalBackdrop />
      <LandingNav onLogin={onLogin} onSignup={onSignup} />
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
        primaryAction={{ label: "Get Started", onClick: onSignup }}
        secondaryAction={{ label: "Log in", onClick: onLogin }}
        marqueeText="JOBAGENT · HUNT · MATCH · APPLY"
      />
      <LandingSections onLogin={onLogin} onSignup={onSignup} />
      <LandingFooter onSignup={onSignup} />
    </div>
  );
}
