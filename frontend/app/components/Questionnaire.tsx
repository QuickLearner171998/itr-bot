"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { QSection } from "../lib/types";

interface Props {
  onDone: (result: any) => void;
  sessionId: string;
}

const STORAGE_KEY = "itr_questionnaire_answers";

export function Questionnaire({ onDone, sessionId }: Props) {
  const [sections, setSections] = useState<QSection[]>([]);
  const [answers, setAnswers] = useState<Record<string, any>>({});
  const [submitting, setSubmitting] = useState(false);
  const [restored, setRestored] = useState(false);

  useEffect(() => {
    api.getQuestionnaire().then((r) => {
      setSections(r.sections);
      const defaults: Record<string, any> = {};
      r.sections.forEach((s: QSection) =>
        s.questions.forEach((q) => {
          if (q.type === "bool") {
            defaults[q.id] = q.extractable ? "unsure" : false;
          } else {
            defaults[q.id] = q.default ?? (q.type === "number" ? 0 : "");
          }
        })
      );
      let saved: Record<string, any> = {};
      try {
        saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      } catch {
        saved = {};
      }
      const merged = { ...defaults, ...saved };
      setAnswers(merged);
      if (Object.keys(saved).length > 0) setRestored(true);
    });
  }, []);

  useEffect(() => {
    if (Object.keys(answers).length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(answers));
    }
  }, [answers]);

  const set = (id: string, v: any) => setAnswers((a) => ({ ...a, [id]: v }));

  const visible = (q: any) => {
    if (!q.depends_on) return true;
    return !!answers[q.depends_on];
  };

  const submit = async () => {
    setSubmitting(true);
    try {
      const result = await api.submitIntake(sessionId, answers);
      onDone(result);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="card">
      <h2>Tell us about your year</h2>
      <p className="sub">
        Answer a few questions so we can pick the right ITR form, build your document
        checklist, and tailor the computation. Not sure about something? Pick
        "Not sure" and we will figure it out from your documents. Nothing is filed yet.
      </p>

      {restored && (
        <div className="restored-note">
          Restored your previous answers.
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              localStorage.removeItem(STORAGE_KEY);
              setRestored(false);
              const defaults: Record<string, any> = {};
              sections.forEach((s) =>
                s.questions.forEach((q) => {
                  if (q.type === "bool") {
                    defaults[q.id] = q.extractable ? "unsure" : false;
                  } else {
                    defaults[q.id] = q.default ?? (q.type === "number" ? 0 : "");
                  }
                })
              );
              setAnswers(defaults);
            }}
          >
            Reset
          </button>
        </div>
      )}

      {sections.map((s) => (
        <div className="q-section" key={s.section}>
          <h3>{s.section}</h3>
          {s.questions.filter(visible).map((q) => (
            <div className="q-item" key={q.id}>
              <div>
                <div className="q-text">{q.text}</div>
                {q.help && <div className="q-help">{q.help}</div>}
              </div>
              <div className="q-control">
                {q.type === "bool" && q.extractable && (
                  <div className="tri" role="radiogroup">
                    {[
                      { v: true, label: "Yes" },
                      { v: false, label: "No" },
                      { v: "unsure", label: "Not sure" },
                    ].map((o) => (
                      <button
                        key={String(o.v)}
                        type="button"
                        className={`tri-opt ${answers[q.id] === o.v ? "on" : ""}`}
                        onClick={() => set(q.id, o.v)}
                        role="radio"
                        aria-checked={answers[q.id] === o.v}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                )}
                {q.type === "bool" && !q.extractable && (
                  <div
                    className={`toggle ${answers[q.id] ? "on" : ""}`}
                    onClick={() => set(q.id, !answers[q.id])}
                    role="switch"
                    aria-checked={!!answers[q.id]}
                  >
                    <span className="knob" />
                  </div>
                )}
                {q.type === "number" && (
                  <input
                    className="field"
                    type="number"
                    value={answers[q.id] ?? 0}
                    onChange={(e) => set(q.id, Number(e.target.value))}
                  />
                )}
                {q.type === "choice" && (
                  <select
                    className="field"
                    value={answers[q.id] ?? ""}
                    onChange={(e) => set(q.id, e.target.value)}
                  >
                    {q.options?.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}

      <div className="btn-row">
        <button className="btn" onClick={submit} disabled={submitting}>
          {submitting ? <span className="spinner" /> : null}
          {submitting ? "Analysing..." : "Build my plan"}
        </button>
      </div>
    </div>
  );
}
