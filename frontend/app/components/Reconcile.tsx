"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface Props {
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

export function Reconcile({ sessionId, onNext, onBack }: Props) {
  const [loading, setLoading] = useState(true);
  const [issues, setIssues] = useState<{ severity: string; message: string }[]>([]);
  const [explanation, setExplanation] = useState("");

  useEffect(() => {
    api.reconcile(sessionId).then((r) => {
      setIssues(r.issues || []);
      setExplanation(r.explanation || "");
      setLoading(false);
    });
  }, [sessionId]);

  return (
    <div className="card">
      <h2>Cross-source reconciliation</h2>
      <p className="sub">
        We compare Form 16, Form 26AS, AIS and broker statements to catch mismatches before
        computing. Anything flagged here is worth a quick look.
      </p>

      {loading ? (
        <div>
          <div className="skeleton-line" style={{ width: "80%", marginBottom: 10 }} />
          <div className="skeleton-line" style={{ width: "60%", marginBottom: 10 }} />
          <div className="skeleton-line" style={{ width: "70%" }} />
        </div>
      ) : (
        <>
          {explanation && <div className="callout" style={{ marginBottom: 16 }}>{explanation}</div>}
          {issues.length === 0 ? (
            <div className="recon-item ok">
              <span className="ri-ic">✓</span>
              <div>All sources reconcile within tolerance. No mismatches found.</div>
            </div>
          ) : (
            issues.map((iss, i) => (
              <div key={i} className={`recon-item ${iss.severity}`}>
                <span className="ri-ic">{iss.severity === "error" ? "✕" : "⚠"}</span>
                <div>{iss.message}</div>
              </div>
            ))
          )}
        </>
      )}

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={onNext} disabled={loading}>Compute my tax</button>
      </div>
    </div>
  );
}
