"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { inr } from "../lib/format";
import { useSession } from "../lib/session";

interface Props {
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

export function Results({ sessionId, onNext, onBack }: Props) {
  const { computeSteps, computation, verification, resetCompute, setComputation } = useSession();
  const [running, setRunning] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    resetCompute();
    setRunning(true);
    api.compute(sessionId).then((r) => {
      if (r.computation) setComputation(r.computation);
      setRunning(false);
    });
  }, [sessionId]);

  const maxAmt = Math.max(1, ...computeSteps.map((s) => Math.abs(s.amount)));

  return (
    <div className="card">
      <h2>Live computation</h2>
      <p className="sub">
        Watch your income flow into tax. All numbers come from a deterministic engine and are
        cross-checked by an independent re-computation.
      </p>

      <div className="waterfall">
        {computeSteps.length === 0 && running && (
          <div className="activity-empty">Starting deterministic engine...</div>
        )}
        {computeSteps.map((s, i) => {
          const cls =
            s.kind === "subtract" ? "wf-subtract" : s.kind === "tax" ? "wf-tax" : s.kind === "total" ? "wf-total" : "wf-add";
          return (
            <div className={`wf-row ${cls} ${s.kind === "total" ? "is-total" : ""}`} key={`${s.key}-${i}`}>
              <span className="wl">{s.label}</span>
              <span className="wbar">
                <span className="wfill" style={{ width: `${(Math.abs(s.amount) / maxAmt) * 100}%` }} />
              </span>
              <span className="wv">
                {s.kind === "subtract" ? "−" : ""}
                {inr(s.amount)}
              </span>
            </div>
          );
        })}
      </div>

      {verification && (
        <div className={`verify-stamp ${verification.verified ? "ok" : "no"}`}>
          {verification.verified ? "✓ Independently verified" : "✕ Verification flag"} · {verification.note}
        </div>
      )}

      {computation && <RegimeSummary computation={computation} />}

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={onNext} disabled={!computation}>
          Get filing walkthrough
        </button>
      </div>
    </div>
  );
}

function RegimeSummary({ computation }: { computation: any }) {
  const { result, regime } = computation;
  const payable = result.refund_or_payable;
  return (
    <div style={{ marginTop: 22 }}>
      <div className="regime-card win">
        <span className="win-tag">{regime.toUpperCase()} regime · from Form 16</span>
        <div className="rl">Total Tax Liability</div>
        <div className="ra">₹{inr(result.total_tax_liability)}</div>
        <div className="guide-note">
          Total income ₹{inr(result.total_income)} ·{" "}
          {payable >= 0 ? "Payable" : "Refund"} ₹{inr(Math.abs(payable))}
        </div>
      </div>
      <div className="callout">
        Computed under the <b>{regime.toUpperCase()} regime</b> (as per your Form 16). The
        figures must be confirmed on the official portal before filing.
      </div>
    </div>
  );
}
