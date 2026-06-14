"use client";

import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { AgentPanel } from "./components/AgentPanel";
import { Checklist } from "./components/Checklist";
import { Guided } from "./components/Guided";
import { Questionnaire } from "./components/Questionnaire";
import { Reconcile } from "./components/Reconcile";
import { Results } from "./components/Results";
import { Stepper } from "./components/Stepper";
import { Upload } from "./components/Upload";
import { useSession } from "./lib/session";

const RESUME_KEY = "itr_session_resume";

function loadResume(): { sessionId: string; step: number; intake: any } | null {
  try {
    const raw = localStorage.getItem(RESUME_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveResume(sessionId: string, step: number, intake: any) {
  try {
    localStorage.setItem(RESUME_KEY, JSON.stringify({ sessionId, step, intake }));
  } catch { /* ignore */ }
}

function clearResume() {
  try { localStorage.removeItem(RESUME_KEY); } catch { /* ignore */ }
}

export default function Home() {
  const { sessionId, start, restoreSession } = useSession();
  const [step, setStep] = useState(0);
  const [intake, setIntake] = useState<any>(null);
  const [starting, setStarting] = useState(false);
  const [resumePrompt, setResumePrompt] = useState<{ sessionId: string; step: number; intake: any } | null>(null);

  // On first mount: check if a prior session exists and the backend still has it.
  useEffect(() => {
    const saved = loadResume();
    if (!saved || saved.step < 1) return;
    api.getState(saved.sessionId)
      .then(() => setResumePrompt(saved))
      .catch(() => clearResume());
  }, []);

  // Persist whenever step / intake changes (after a session is active).
  useEffect(() => {
    if (sessionId && step >= 1) saveResume(sessionId, step, intake);
  }, [sessionId, step, intake]);

  const navigate = (s: number) => setStep(s);

  const begin = async () => {
    clearResume();
    setStarting(true);
    await start();
    setStep(1);
    setStarting(false);
  };

  const resume = () => {
    if (!resumePrompt) return;
    restoreSession(resumePrompt.sessionId);
    setIntake(resumePrompt.intake);
    setStep(resumePrompt.step);
    setResumePrompt(null);
  };

  const startFresh = () => {
    clearResume();
    setResumePrompt(null);
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

          {resumePrompt && (
            <div className="resume-banner">
              <span>You have a session in progress (step {resumePrompt.step} of 6).</span>
              <div className="resume-actions">
                <button className="btn" onClick={resume}>Resume where I left off</button>
                <button className="btn ghost" onClick={startFresh}>Start fresh</button>
              </div>
            </div>
          )}

          {!resumePrompt && (
            <button className="btn" onClick={begin} disabled={starting}>
              {starting ? <span className="spinner" /> : null}
              {starting ? "Starting..." : "Start filing assistant"}
            </button>
          )}

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
              navigate(2);
            }}
          />
        );
      case 2:
        return (
          <Checklist
            decision={intake.decision}
            checklist={intake.checklist}
            summary={intake.summary}
            onBack={() => navigate(1)}
            onNext={() => navigate(3)}
          />
        );
      case 3:
        return (
          <Upload
            checklist={intake.checklist}
            sessionId={sessionId}
            onBack={() => navigate(2)}
            onNext={() => navigate(4)}
          />
        );
      case 4:
        return <Reconcile sessionId={sessionId} onBack={() => navigate(3)} onNext={() => navigate(5)} />;
      case 5:
        return <Results sessionId={sessionId} onBack={() => navigate(4)} onNext={() => navigate(6)} />;
      case 6:
        return <Guided sessionId={sessionId} onBack={() => navigate(5)} />;
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
