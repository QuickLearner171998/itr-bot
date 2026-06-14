"use client";

import { docIcon } from "../lib/format";
import type { ChecklistItem, FormDecision } from "../lib/types";

interface Props {
  decision: FormDecision;
  checklist: ChecklistItem[];
  summary: string;
  onNext: () => void;
  onBack: () => void;
}

export function Checklist({ decision, checklist, summary, onNext, onBack }: Props) {
  return (
    <div className="card">
      <h2>Your filing plan</h2>
      <p className="sub">
        Based on your answers we recommend <b style={{ color: "var(--accent-2)" }}>{decision.form}</b>.
        Gather the documents below; we show exactly how to get each one.
      </p>

      {summary && <div className="callout" style={{ marginBottom: 18 }}>{summary}</div>}

      <div className="card" style={{ background: "rgba(255,255,255,0.02)", marginBottom: 18 }}>
        <h3 style={{ fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--accent-2)", marginBottom: 10 }}>
          Why {decision.form}?
        </h3>
        <ul style={{ margin: "0 0 0 18px", color: "var(--text-dim)", fontSize: 14, lineHeight: 1.7 }}>
          {decision.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </div>

      {checklist.map((item) => (
        <div className="doc-item" key={item.doc_type}>
          <div className="doc-badge">{docIcon(item.doc_type)}</div>
          <div className="doc-meta">
            <h4>
              {item.title}
              <span className={`req-chip ${item.required ? "req" : "opt"}`}>
                {item.required ? "Required" : "If applicable"}
              </span>
            </h4>
            <div className="why">{item.why}</div>
            <div style={{ fontSize: 12, color: "var(--text-faint)" }}>Source: {item.source}</div>
            <ol>
              {item.how_to_get.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </div>
        </div>
      ))}

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={onNext}>I have my documents</button>
      </div>
    </div>
  );
}
