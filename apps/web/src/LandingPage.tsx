import { LiquidMetalHero } from "./components/LiquidMetalHero";

type Props = {
  onLogin: () => void;
  onSignup: () => void;
};

export function LandingPage({ onLogin, onSignup }: Props) {
  return (
    <LiquidMetalHero
      headline={
        <>
          Your AI job agent.
          <br />
          Always hunting.
        </>
      }
      subtitle="Upload a resume. We find fits, score them, and apply when you say go."
      primaryAction={{ label: "Get started", onClick: onSignup }}
      secondaryAction={{ label: "Log in", onClick: onLogin }}
      marqueeText="JOBAGENT · HUNT · MATCH · APPLY"
    />
  );
}
