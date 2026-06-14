"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { GuideSection } from "../lib/types";

interface Props {
  sessionId: string;
  onBack: () => void;
}

export function Guided({ sessionId, onBack }: Props) {
  const [loading, setLoading] = useState(true);
  const [intro, setIntro] = useState("");
  const [sections, setSections] = useState<GuideSection[]>([]);
  const [regime, setRegime] = useState("");
  const [form, setForm] = useState("");

  useEffect(() => {
    api.guidance(sessionId).then((r) => {
      setIntro(r.intro || "");
      setSections(r.sections || []);
      setRegime(r.regime || "");
      setForm(r.form || "");
      setLoading(false);
    });
  }, [sessionId]);

  if (loading) {
    return (
      <div className="card">
        <h2>Preparing your walkthrough...</h2>
        <div className="skeleton-line" style={{ width: "90%", margin: "16px 0" }} />
        <div className="skeleton-line" style={{ width: "75%", marginBottom: 12 }} />
        <div className="skeleton-line" style={{ width: "85%" }} />
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Guided filing walkthrough</h2>
      <p className="sub">
        Filing <b>{form}</b> under the <b>{regime} regime</b> on incometax.gov.in. The portal
        pre-fills much of this — verify each value against the numbers below and copy where needed.
      </p>

      {intro && <div className="callout" style={{ marginBottom: 18 }}>{intro}</div>}

      {sections.map((s, i) => (
        <div className="guide-section" key={i}>
          <div className="guide-head">
            <h4>{s.title}</h4>
            <div className="path">{s.portal_path}</div>
          </div>
          <div className="guide-body">
            {s.fields.map((f, j) => (
              <CopyField key={j} label={f.label} value={f.value} note={f.note} />
            ))}
            {s.note && <div className="guide-note">{s.note}</div>}
          </div>
        </div>
      ))}

      <div className="callout warn" style={{ marginTop: 8 }}>
        Reminder: after submitting, e-verify within 30 days (Aadhaar OTP or net-banking), or the
        return is treated as not filed.
      </div>

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
      </div>
    </div>
  );
}

function CopyField({ label, value, note }: { label: string; value: string; note: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className="guide-field">
      <span className="gl">
        {label}
        {note && <span className="note">{note}</span>}
      </span>
      <span className="gv">
        <b>{value}</b>
        <button className={`copy-btn ${copied ? "copied" : ""}`} onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </span>
    </div>
  );
}
