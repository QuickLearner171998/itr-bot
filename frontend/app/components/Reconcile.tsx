"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { confClass, confLabel, docIcon, inr } from "../lib/format";
import { useSession } from "../lib/session";

interface Props {
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

export function Reconcile({ sessionId, onNext, onBack }: Props) {
  const { extractions } = useSession();
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

  const docs = Object.values(extractions).flatMap((v) => (Array.isArray(v) ? v : [v]));

  return (
    <div className="card">
      <h2>Review extracted data</h2>
      <p className="sub">
        Here is exactly what we read from each document. Please confirm these values look right
        before we compute your tax. Nothing is computed until you continue.
      </p>

      {docs.length === 0 ? (
        <div className="recon-item warning" style={{ marginBottom: 20 }}>
          <span className="ri-ic">⚠</span>
          <div>No extracted documents found. Go back and upload your documents first.</div>
        </div>
      ) : (
        <div className="extract-summary">
          {docs.map((doc, di) => {
            const filled = doc.fields.filter((f) => f.value !== null && f.value !== "");
            return (
              <div className="extract-summary-card" key={`${doc.doc_type}-${di}`}>
                <div className="ess-head">
                  <span className="ess-title">
                    {docIcon(doc.doc_type)} {doc.filename || doc.doc_type}
                  </span>
                  <span className={`badge ${doc.status}`}>{doc.status.replace("_", " ")}</span>
                </div>
                {filled.length === 0 ? (
                  <div className="ess-empty">No values extracted.</div>
                ) : (
                  filled.map((f) => (
                    <div className="ess-row" key={f.name}>
                      <span className="ess-label" title={f.source_hint || ""}>{f.label}</span>
                      <span className="ess-val">
                        {typeof f.value === "number" ? `₹${inr(f.value)}` : f.value}
                        <span className={`conf-chip ${confClass(f.confidence)}`}>
                          {confLabel(f.confidence)}
                        </span>
                      </span>
                    </div>
                  ))
                )}
              </div>
            );
          })}
        </div>
      )}

      <h3 style={{ marginTop: 8 }}>Cross-source reconciliation</h3>
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
