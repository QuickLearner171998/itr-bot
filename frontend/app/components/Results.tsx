"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { inr } from "../lib/format";
import { useSession } from "../lib/session";
import type { RegimeComparison, RegimeResult } from "../lib/types";

interface Props {
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

export function Results({ sessionId, onNext, onBack }: Props) {
  const {
    computeSteps, computation, comparison, verification,
    resetCompute, setComputation, setComparison,
  } = useSession();
  const [running, setRunning] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    resetCompute();
    setRunning(true);
    api.compute(sessionId).then((r) => {
      if (r.computation) setComputation(r.computation);
      if (r.comparison) setComparison(r.comparison);
      setRunning(false);
    });
  }, [sessionId]);

  const hasDetails = computeSteps.some((s) => s.detail);
  const stepFor = (k: string) => computeSteps.find((s) => s.key === k);
  const taxable = stepFor("total_income");
  const totalTax = stepFor("total_tax");
  const net = stepFor("net");
  const CHIP: Record<string, string> = { add: "+", subtract: "\u2212", tax: "%", total: "=" };

  return (
    <div className="card">
      <h2>Live computation</h2>
      <p className="sub">
        Your income flows step by step into tax. Every number comes from a deterministic engine and
        is cross-checked by an independent re-computation — click any line to see the exact formula.
      </p>

      <div className="mile-rail">
        <div className={`mile ${taxable ? "live" : "pending"}`}>
          <div className="mk">Taxable Income</div>
          {taxable ? <div className="mv">₹{inr(taxable.amount)}</div> : <div className="mvph">···</div>}
        </div>
        <div className={`mile tax ${totalTax ? "live" : "pending"}`}>
          <div className="mk">Total Tax Liability</div>
          {totalTax ? <div className="mv">₹{inr(totalTax.amount)}</div> : <div className="mvph">···</div>}
        </div>
        <div className={`mile net ${net && net.label !== "Tax Payable" ? "" : "due"} ${net ? "live" : "pending"}`}>
          <div className="mk">{net ? net.label : "Payable / Refund"}</div>
          {net ? <div className="mv">₹{inr(net.amount)}</div> : <div className="mvph">···</div>}
        </div>
      </div>

      {hasDetails && (
        <button className="calc-toggle" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Hide calculation details" : "Show calculation details"}
        </button>
      )}

      <div className="waterfall">
        {computeSteps.length === 0 && running && (
          <div className="activity-empty">Starting deterministic engine...</div>
        )}
        {computeSteps.map((s, i) => {
          if (s.kind === "info") {
            return (
              <div className="wf-section-header" key={`${s.key}-${i}`}>
                {s.label}
              </div>
            );
          }
          const cls =
            s.kind === "subtract" ? "wf-subtract" : s.kind === "tax" ? "wf-tax" : s.kind === "total" ? "wf-total" : "wf-add";
          const rowKey = `${s.key}-${i}`;
          const hasDetail = !!s.detail;
          const isOpen = hasDetail && (showAll || open[rowKey]);
          return (
            <div className="wf-item" key={rowKey}>
              <div
                className={`wf-row ${cls} ${s.kind === "total" ? "is-total" : ""} ${hasDetail ? "has-detail" : ""}`}
                onClick={hasDetail ? () => setOpen((o) => ({ ...o, [rowKey]: !o[rowKey] })) : undefined}
              >
                <span className={`wf-chip`}>{CHIP[s.kind] || "+"}</span>
                <span className="wl">
                  {s.label.trim()}
                  {hasDetail && <span className="calc-caret">{isOpen ? "\u25be" : "\u25b8"}</span>}
                </span>
                <span className="wv">
                  {s.kind === "subtract" ? "\u2212" : ""}
                  {inr(s.amount)}
                </span>
              </div>
              {isOpen && <div className="wf-detail">{s.detail}</div>}
            </div>
          );
        })}
      </div>

      {verification && (
        <div className={`verify-stamp ${verification.verified ? "ok" : "no"}`}>
          {verification.verified ? "\u2713 Independently verified" : "\u2715 Verification flag"} · {verification.note}
        </div>
      )}

      {computation && <RegimeSummary computation={computation} />}

      {comparison && <RegimeCompare comparison={comparison} filed={computation?.regime} />}

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

const COMPARE_ROWS: { label: string; key: keyof RegimeResult }[] = [
  { label: "Gross Total Income", key: "gross_total_income" },
  { label: "Total Deductions (Ch. VI-A)", key: "total_deductions" },
  { label: "Taxable Income", key: "total_income" },
  { label: "Tax before Rebate", key: "tax_before_rebate" },
  { label: "Less: Rebate u/s 87A", key: "rebate_87a" },
  { label: "Add: Surcharge", key: "surcharge" },
  { label: "Add: Cess (4%)", key: "cess" },
  { label: "Total Tax Liability", key: "total_tax_liability" },
  { label: "Less: Taxes Already Paid", key: "taxes_paid" },
];

function RegimeCompare({ comparison, filed }: { comparison: RegimeComparison; filed?: string }) {
  const { old: oldR, new: newR, recommended, savings } = comparison;
  const net = (r: RegimeResult) =>
    r.refund_or_payable >= 0
      ? `Payable ₹${inr(r.refund_or_payable)}`
      : `Refund ₹${inr(Math.abs(r.refund_or_payable))}`;

  return (
    <div className="compare" style={{ marginTop: 26 }}>
      <h3 className="compare-title">Old vs New regime — side by side</h3>
      <p className="sub">
        Same income and proofs, computed under both regimes. The lower total tax is recommended.
      </p>

      <div className={`compare-banner ${recommended === "old" ? "old" : "new"}`}>
        {savings > 0 ? (
          <>
            <b>{recommended.toUpperCase()} regime</b> saves you{" "}
            <b>₹{inr(savings)}</b> in tax.
          </>
        ) : (
          <>Both regimes result in the same tax liability.</>
        )}
      </div>

      <div className="compare-table">
        <div className="compare-head">
          <span className="cl" />
          <span className={`ch ${recommended === "old" ? "best" : ""}`}>
            OLD{recommended === "old" ? " · best" : ""}
            {filed === "old" ? " · filing" : ""}
          </span>
          <span className={`ch ${recommended === "new" ? "best" : ""}`}>
            NEW{recommended === "new" ? " · best" : ""}
            {filed === "new" ? " · filing" : ""}
          </span>
        </div>
        {COMPARE_ROWS.map((row) => {
          const ov = oldR[row.key] as number;
          const nv = newR[row.key] as number;
          const isTotal = row.key === "total_tax_liability";
          return (
            <div className={`compare-row ${isTotal ? "is-total" : ""}`} key={row.key}>
              <span className="cl">{row.label}</span>
              <span className={`cv ${recommended === "old" ? "best" : ""}`}>₹{inr(ov)}</span>
              <span className={`cv ${recommended === "new" ? "best" : ""}`}>₹{inr(nv)}</span>
            </div>
          );
        })}
        <div className="compare-row is-net">
          <span className="cl">Net Position</span>
          <span className={`cv ${recommended === "old" ? "best" : ""}`}>{net(oldR)}</span>
          <span className={`cv ${recommended === "new" ? "best" : ""}`}>{net(newR)}</span>
        </div>
      </div>
      <div className="guide-note">
        New regime uses lower slab rates but allows almost no deductions; the old regime has
        higher rates but full Chapter VI-A deductions. The engine picks the cheaper one above.
      </div>
    </div>
  );
}
