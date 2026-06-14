"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { confClass, confLabel, docIcon, inr } from "../lib/format";
import { useSession } from "../lib/session";
import type { Discrepancy, TaxInput } from "../lib/types";

interface Props {
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

interface FieldDef {
  path: string;
  label: string;
}

interface Group {
  title: string;
  fields: FieldDef[];
}

// Editable consolidated fields, grouped. Document extraction prefills these;
// the user can override any value before computing.
const GROUPS: Group[] = [
  {
    title: "Other income",
    fields: [
      { path: "savings_interest", label: "Savings bank interest" },
      { path: "fd_interest", label: "FD / RD interest" },
      { path: "dividend", label: "Dividend" },
      { path: "family_pension", label: "Family pension" },
      { path: "other_income", label: "Other income" },
    ],
  },
  {
    title: "House property & HRA",
    fields: [
      { path: "let_out_annual_rent", label: "Let-out annual rent" },
      { path: "let_out_municipal_taxes", label: "Municipal taxes paid" },
      { path: "hra_received", label: "HRA received" },
      { path: "hra_rent_paid", label: "Rent paid" },
      { path: "hra_basic_da", label: "Basic + DA" },
    ],
  },
  {
    title: "Capital gains",
    fields: [
      { path: "capital_gains.stcg_111a", label: "STCG 111A (equity)" },
      { path: "capital_gains.ltcg_112a", label: "LTCG 112A (equity)" },
      { path: "capital_gains.stcg_other", label: "STCG (slab rate)" },
      { path: "capital_gains.ltcg_other", label: "LTCG (other)" },
      { path: "capital_gains.vda_gain", label: "Crypto / VDA gains" },
    ],
  },
  {
    title: "Deductions (Chapter VI-A)",
    fields: [
      { path: "deductions.amount_80c", label: "80C" },
      { path: "deductions.amount_80ccd1b", label: "80CCD(1B) NPS self" },
      { path: "deductions.amount_80ccd2", label: "80CCD(2) employer NPS" },
      { path: "deductions.amount_80d_self", label: "80D self/family" },
      { path: "deductions.amount_80d_parents", label: "80D parents" },
      { path: "deductions.home_loan_interest", label: "Home loan interest (24b)" },
      { path: "deductions.amount_80e", label: "80E education loan" },
    ],
  },
  {
    title: "Taxes paid & relief",
    fields: [
      { path: "tds_total", label: "Total TDS" },
      { path: "advance_tax", label: "Advance tax" },
      { path: "self_assessment_tax", label: "Self-assessment tax" },
      { path: "relief_89", label: "Relief u/s 89 (arrears)" },
      { path: "relief_90_91", label: "Relief u/s 90/91 (foreign)" },
    ],
  },
  {
    title: "Other",
    fields: [
      { path: "agricultural_income", label: "Agricultural income" },
      { path: "brought_forward_loss", label: "Brought-forward loss" },
    ],
  },
];

function getPath(obj: any, path: string): number {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj) ?? 0;
}

function setPath(obj: any, path: string, value: number): any {
  const clone = structuredClone(obj);
  const keys = path.split(".");
  let cur = clone;
  for (let i = 0; i < keys.length - 1; i++) cur = cur[keys[i]];
  cur[keys[keys.length - 1]] = value;
  return clone;
}

export function Reconcile({ sessionId, onNext, onBack }: Props) {
  const { extractions } = useSession();
  const [ti, setTi] = useState<TaxInput | null>(null);
  const [discrepancies, setDiscrepancies] = useState<Discrepancy[]>([]);
  const [issues, setIssues] = useState<{ severity: string; message: string }[]>([]);
  const [explanation, setExplanation] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.consolidatePreview(sessionId).then((r) => {
      setTi(r.tax_input);
      setDiscrepancies(r.discrepancies || []);
    });
    api.reconcile(sessionId).then((r) => {
      setIssues(r.issues || []);
      setExplanation(r.explanation || "");
      setLoading(false);
    });
  }, [sessionId]);

  const discByField = useMemo(() => {
    const m: Record<string, Discrepancy> = {};
    for (const d of discrepancies) m[d.field] = d;
    return m;
  }, [discrepancies]);

  const update = (path: string, raw: string) => {
    if (!ti) return;
    const value = Number(raw.replace(/,/g, "")) || 0;
    setTi(setPath(ti, path, value));
  };

  const saveAndContinue = async () => {
    if (!ti) return;
    setSaving(true);
    try {
      await api.saveTaxInput(sessionId, ti);
      onNext();
    } finally {
      setSaving(false);
    }
  };

  const docs = Object.values(extractions).flatMap((v) => (Array.isArray(v) ? v : [v]));

  return (
    <div className="card">
      <h2>Review &amp; confirm your figures</h2>
      <p className="sub">
        These values are prefilled from your documents. Nothing is assumed — wherever your
        documents disagree we flag it for you. Edit any number, then continue. We compute only
        on what you confirm here.
      </p>

      {ti && (
        <div className="regime-chip-row">
          <span className="tag req">Regime: {ti.filing_regime.toUpperCase()} (from Form 16)</span>
          {discrepancies.length > 0 && (
            <span className="tag warn-tag">{discrepancies.length} value(s) to confirm</span>
          )}
        </div>
      )}

      {!ti ? (
        <div>
          <div className="skeleton-line" style={{ width: "70%", marginBottom: 10 }} />
          <div className="skeleton-line" style={{ width: "55%" }} />
        </div>
      ) : (
        <div className="review-groups">
          {ti.salaries.length > 0 && (
            <div className="review-group">
              <h4>Salary</h4>
              {ti.salaries.map((s, i) => (
                <div key={i} className="review-employer">
                  <div className="re-name">{s.employer_name || `Employer ${i + 1}`}</div>
                  <EditRow label="Gross salary" path={`salaries.${i}.gross_salary`} ti={ti} onChange={update} />
                  <EditRow label="Exempt allowances (Sec 10)" path={`salaries.${i}.exempt_allowances`} ti={ti} onChange={update} />
                  <EditRow label="Professional tax" path={`salaries.${i}.professional_tax`} ti={ti} onChange={update} />
                  <EditRow label="TDS" path={`salaries.${i}.tds`} ti={ti} onChange={update} />
                </div>
              ))}
            </div>
          )}

          {GROUPS.map((g) => (
            <div className="review-group" key={g.title}>
              <h4>{g.title}</h4>
              {g.fields.map((f) => (
                <EditRow
                  key={f.path}
                  label={f.label}
                  path={f.path}
                  ti={ti}
                  onChange={update}
                  discrepancy={discByField[f.path]}
                />
              ))}
            </div>
          ))}
        </div>
      )}

      <h3 style={{ marginTop: 16 }}>Cross-source reconciliation</h3>
      {loading ? (
        <div>
          <div className="skeleton-line" style={{ width: "80%", marginBottom: 10 }} />
          <div className="skeleton-line" style={{ width: "60%" }} />
        </div>
      ) : (
        <>
          {explanation && <div className="callout" style={{ marginBottom: 16 }}>{explanation}</div>}
          {issues.length === 0 ? (
            <div className="recon-item ok">
              <span className="ri-ic">✓</span>
              <div>All sources reconcile within tolerance.</div>
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

      {docs.length > 0 && (
        <details className="raw-extracts">
          <summary>View raw extracted values per document ({docs.length})</summary>
          <div className="extract-summary">
            {docs.map((doc, di) => {
              const filled = doc.fields.filter((f) => f.value !== null && f.value !== "");
              return (
                <div className="extract-summary-card" key={`${doc.doc_type}-${di}`}>
                  <div className="ess-head">
                    <span className="ess-title">{docIcon(doc.doc_type)} {doc.filename || doc.doc_type}</span>
                    <span className={`badge ${doc.status}`}>{doc.status.replace("_", " ")}</span>
                  </div>
                  {filled.map((f) => (
                    <div className="ess-row" key={f.name}>
                      <span className="ess-label" title={f.source_hint || ""}>{f.label}</span>
                      <span className="ess-val">
                        {typeof f.value === "number" ? `\u20b9${inr(f.value)}` : f.value}
                        <span className={`conf-chip ${confClass(f.confidence)}`}>{confLabel(f.confidence)}</span>
                      </span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </details>
      )}

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={saveAndContinue} disabled={loading || saving || !ti}>
          {saving ? "Saving..." : "Confirm & compute my tax"}
        </button>
      </div>
    </div>
  );
}

function EditRow({
  label,
  path,
  ti,
  onChange,
  discrepancy,
}: {
  label: string;
  path: string;
  ti: TaxInput;
  onChange: (path: string, raw: string) => void;
  discrepancy?: Discrepancy;
}) {
  return (
    <div className={`edit-row ${discrepancy ? "flagged" : ""}`}>
      <span className="er-label">
        {label}
        {discrepancy && (
          <span
            className="er-flag"
            title={discrepancy.sources.map((s) => `${s.doc}: \u20b9${inr(s.value)}`).join("  vs  ")}
          >
            ⚠ {discrepancy.sources.map((s) => `${s.doc} \u20b9${inr(s.value)}`).join(" vs ")}
          </span>
        )}
      </span>
      <input
        className="field er-input"
        inputMode="numeric"
        value={getPath(ti, path)}
        onChange={(e) => onChange(path, e.target.value)}
      />
    </div>
  );
}
