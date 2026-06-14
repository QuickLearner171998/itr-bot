"use client";

import { useState } from "react";
import { AgentPanel } from "./components/AgentPanel";
import { Checklist } from "./components/Checklist";
import { Guided } from "./components/Guided";
import { Questionnaire } from "./components/Questionnaire";
import { Reconcile } from "./components/Reconcile";
import { Results } from "./components/Results";
import { Stepper } from "./components/Stepper";
import { Upload } from "./components/Upload";
import { useSession } from "./lib/session";

export default function Home() {
  const { sessionId, start } = useSession();
  const [step, setStep] = useState(0);
  const [intake, setIntake] = useState<any>(null);
  const [starting, setStarting] = useState(false);

  const begin = async () => {
    setStarting(true);
    await start();
    setStep(1);
    setStarting(false);
  };

  if (step === 0 || !sessionId) {
    return (
      <main className="container">
        <section className="hero">
          <h1>
            File your ITR with <span className="grad">confident automation</span>
          </h1>
          <p>
            Built for salaried individuals. We read your Form 16, AIS, 26AS and broker
            statements with AI document intelligence, run a deterministic, independently
            verified tax computation, and guide you screen-by-screen on the portal.
          </p>
          <button className="btn" onClick={begin} disabled={starting}>
            {starting ? <span className="spinner" /> : null}
            {starting ? "Starting..." : "Start filing assistant"}
          </button>

          <div className="feature-grid">
            <div className="card feature">
              <div className="ic">🧠</div>
              <h3>Real-time document intelligence</h3>
              <p>Watch every field stream in with confidence scores and self-critique.</p>
            </div>
            <div className="card feature">
              <div className="ic">🧮</div>
              <h3>Deterministic, verified math</h3>
              <p>Computed for your regime (from Form 16), then re-computed independently to prove correctness.</p>
            </div>
            <div className="card feature">
              <div className="ic">🧭</div>
              <h3>Guided portal filing</h3>
              <p>Copy-paste, schedule-by-schedule, mirroring incometax.gov.in.</p>
            </div>
          </div>
        </section>
      </main>
    );
  }

  const showPanel = step >= 3;

  const content = (() => {
    switch (step) {
      case 1:
        return (
          <Questionnaire
            sessionId={sessionId}
            onDone={(r) => {
              setIntake(r);
              setStep(2);
            }}
          />
        );
      case 2:
        return (
          <Checklist
            decision={intake.decision}
            checklist={intake.checklist}
            summary={intake.summary}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
          />
        );
      case 3:
        return (
          <Upload
            checklist={intake.checklist}
            sessionId={sessionId}
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
          />
        );
      case 4:
        return <Reconcile sessionId={sessionId} onBack={() => setStep(3)} onNext={() => setStep(5)} />;
      case 5:
        return <Results sessionId={sessionId} onBack={() => setStep(4)} onNext={() => setStep(6)} />;
      case 6:
        return <Guided sessionId={sessionId} onBack={() => setStep(5)} />;
      default:
        return null;
    }
  })();

  return (
    <main className="container">
      <div style={{ paddingTop: 24 }}>
        <Stepper current={step} />
      </div>
      {showPanel ? (
        <div className="work-grid">
          <div>{content}</div>
          <AgentPanel />
        </div>
      ) : (
        <div style={{ paddingBottom: 80 }}>{content}</div>
      )}
    </main>
  );
}
