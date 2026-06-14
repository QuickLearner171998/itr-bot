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

      {computation && <RegimeCompare computation={computation} />}

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={onNext} disabled={!computation}>
          Get filing walkthrough
        </button>
      </div>
    </div>
  );
}

function RegimeCompare({ computation }: { computation: any }) {
  const { old, new: nw, recommended_regime, recommended_savings } = computation;
  const maxTax = Math.max(1, old.total_tax_liability, nw.total_tax_liability);

  const Card = ({ r, name }: { r: any; name: string }) => {
    const win = recommended_regime === r.regime;
    const payable = r.refund_or_payable;
    return (
      <div className={`regime-card ${win ? "win" : ""}`}>
        {win && <span className="win-tag">Recommended</span>}
        <div className="rl">{name}</div>
        <div className="ra">₹{inr(r.total_tax_liability)}</div>
        <div className="regime-bars">
          <div
            className="bf"
            style={{
              width: `${(r.total_tax_liability / maxTax) * 100}%`,
              background: win
                ? "linear-gradient(90deg, var(--accent-2), var(--good))"
                : "linear-gradient(90deg, var(--accent), var(--accent-3))",
            }}
          />
        </div>
        <div className="guide-note">
          Total income ₹{inr(r.total_income)} · {payable >= 0 ? "Payable" : "Refund"} ₹{inr(Math.abs(payable))}
        </div>
      </div>
    );
  };

  return (
    <div style={{ marginTop: 22 }}>
      <div className="regime-grid">
        <Card r={old} name="Old Regime" />
        <Card r={nw} name="New Regime (default)" />
      </div>
      <div className="callout">
        We recommend the <b>{recommended_regime.toUpperCase()} regime</b> — it saves you about{" "}
        <b style={{ color: "var(--good)" }}>₹{inr(recommended_savings)}</b> in tax.
      </div>
    </div>
  );
}
