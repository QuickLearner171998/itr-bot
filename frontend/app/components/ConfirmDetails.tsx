"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Question } from "../lib/types";

interface Props {
  sessionId: string;
  onNext: (decision: any) => void;
  onBack: () => void;
}

interface GapData {
  regime: string;
  inferred: string[];
  gap_questions: Question[];
  suggested_docs: { doc_type: string; title: string; why: string }[];
}

function defaultAnswers(questions: Question[]): Record<string, any> {
  const a: Record<string, any> = {};
  for (const q of questions) {
    a[q.id] = q.type === "bool" ? false : q.default ?? (q.type === "number" ? 0 : "");
  }
  return a;
}

export function ConfirmDetails({ sessionId, onNext, onBack }: Props) {
  const [data, setData] = useState<GapData | null>(null);
  const [answers, setAnswers] = useState<Record<string, any>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.analyzeGaps(sessionId).then((d: GapData) => {
      setData(d);
      setAnswers(defaultAnswers(d.gap_questions));
    });
  }, [sessionId]);

  const set = (id: string, value: any) => setAnswers((a) => ({ ...a, [id]: value }));

  const submit = async () => {
    setSubmitting(true);
    try {
      const res = await api.submitGaps(sessionId, answers);
      onNext(res.decision);
    } finally {
      setSubmitting(false);
    }
  };

  if (!data) {
    return (
      <div className="card">
        <h2>Reading your documents...</h2>
        <div className="skeleton-line" style={{ width: "70%", marginBottom: 10 }} />
        <div className="skeleton-line" style={{ width: "55%", marginBottom: 10 }} />
        <div className="skeleton-line" style={{ width: "60%" }} />
      </div>
    );
  }

  return (
    <div className="card">
      <h2>A few things we couldn&apos;t read from your documents</h2>
      <p className="sub">
        We already pulled everything we could from your Form 16, AIS and 26AS. Just confirm the
        items below (most people leave them as-is) and we&apos;ll pick the right ITR form.
      </p>

      <div className="inferred-box">
        <div className="ib-title">What we found in your documents</div>
        <ul>
          {data.inferred.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </div>

      {data.suggested_docs.length > 0 && (
        <div className="callout" style={{ marginBottom: 16 }}>
          {data.suggested_docs.map((s) => (
            <div key={s.doc_type}>
              <strong>Optional:</strong> {s.why}{" "}
              <button className="link-btn" onClick={onBack}>Add {s.title}</button>
            </div>
          ))}
        </div>
      )}

      <div className="gap-form">
        {data.gap_questions.map((q) => (
          <div className="gap-q" key={q.id}>
            <label>
              <span className="gq-text">{q.text}</span>
              {q.help && <span className="gq-help">{q.help}</span>}
            </label>
            {q.type === "bool" ? (
              <div className="seg">
                <button
                  className={answers[q.id] === false ? "active" : ""}
                  onClick={() => set(q.id, false)}
                >
                  No
                </button>
                <button
                  className={answers[q.id] === true ? "active" : ""}
                  onClick={() => set(q.id, true)}
                >
                  Yes
                </button>
              </div>
            ) : q.type === "choice" ? (
              <select
                className="field"
                value={answers[q.id] ?? ""}
                onChange={(e) => set(q.id, e.target.value)}
              >
                {q.options?.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input
                className="field"
                inputMode="numeric"
                value={answers[q.id] ?? 0}
                onChange={(e) => set(q.id, Number(e.target.value) || 0)}
                style={{ width: 100 }}
              />
            )}
          </div>
        ))}
      </div>

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back to documents</button>
        <button className="btn" onClick={submit} disabled={submitting}>
          {submitting ? "Saving..." : "Continue to review"}
        </button>
      </div>
    </div>
  );
}
