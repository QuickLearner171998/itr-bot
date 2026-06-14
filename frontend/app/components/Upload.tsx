"use client";

import { useRef, useState } from "react";
import { api } from "../lib/api";
import { confClass, confLabel, docIcon, inr } from "../lib/format";
import { useSession } from "../lib/session";
import type { ChecklistItem, ExtractedField } from "../lib/types";

interface Props {
  checklist: ChecklistItem[];
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

export function Upload({ checklist, sessionId, onNext, onBack }: Props) {
  const { liveDocs, extractions } = useSession();
  const uploadedCount = Object.keys(extractions).length;

  return (
    <div className="card">
      <h2>Upload &amp; watch extraction</h2>
      <p className="sub">
        Drop each document below. Our document-intelligence agent extracts every field in
        real time, shows its confidence, and self-checks low-confidence values. Review and
        correct anything before we compute.
      </p>

      <div className="upload-grid">
        {checklist.map((item) => (
          <ExtractCard key={item.doc_type} item={item} sessionId={sessionId} />
        ))}
      </div>

      <div className="btn-row">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={onNext} disabled={uploadedCount === 0}>
          Continue ({uploadedCount} uploaded)
        </button>
      </div>
    </div>
  );
}

function ExtractCard({ item, sessionId }: { item: ChecklistItem; sessionId: string }) {
  const { liveDocs, extractions } = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [password, setPassword] = useState("");
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);

  const live = liveDocs[item.doc_type];
  const extraction = extractions[item.doc_type];
  const needsPassword = item.doc_type === "ais";

  const liveFields: ExtractedField[] = extraction
    ? extraction.fields
    : live
    ? Object.values(live.fields)
    : [];

  const status = extraction?.status || live?.status;
  const confidence = extraction?.overall_confidence ?? live?.confidence ?? 0;

  const onFile = async (file: File) => {
    setUploading(true);
    setSaved(false);
    try {
      await api.uploadDocument(sessionId, item.doc_type, file, password || undefined);
    } finally {
      setUploading(false);
    }
  };

  const saveReview = async () => {
    if (Object.keys(edits).length === 0) return;
    await api.reviewDocument(sessionId, item.doc_type, edits);
    setSaved(true);
  };

  return (
    <div className="extract-card">
      <div className="extract-head">
        <span className="name">
          {docIcon(item.doc_type)} {item.title}
        </span>
        {status && <span className={`badge ${status}`}>{status.replace("_", " ")}</span>}
      </div>

      {!extraction && !live && (
        <>
          {needsPassword && (
            <input
              className="field"
              style={{ width: "100%", marginBottom: 8 }}
              placeholder="AIS PDF password (PAN + DOB)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
          <div
            className={`dropzone ${uploading ? "has" : ""}`}
            onClick={() => inputRef.current?.click()}
          >
            {uploading ? (
              <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                <span className="spinner" style={{ borderTopColor: "var(--accent)" }} /> Extracting...
              </span>
            ) : (
              <>
                <div>Click to upload PDF or image</div>
                <div className="hint">{item.required ? "Required" : "Optional"} · {item.source}</div>
              </>
            )}
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,image/*"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
            />
          </div>
        </>
      )}

      {liveFields.length > 0 && (
        <div>
          {liveFields.map((f) => (
            <div className="field-row" key={f.name}>
              <span className="fl" title={f.source_hint || ""}>
                {f.label}
              </span>
              <span className="fv">
                {extraction ? (
                  <input
                    className="field"
                    style={{ width: 130, textAlign: "right" }}
                    defaultValue={f.value ?? ""}
                    onChange={(e) => setEdits((s) => ({ ...s, [f.name]: e.target.value }))}
                  />
                ) : (
                  <span>{typeof f.value === "number" ? inr(f.value) : f.value ?? "-"}</span>
                )}
                <span className={`conf-chip ${confClass(f.confidence)}`}>{confLabel(f.confidence)}</span>
              </span>
            </div>
          ))}

          {extraction && (
            <>
              {extraction.issues.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  {extraction.issues.map((iss, i) => (
                    <div key={i} className="guide-note" style={{ color: iss.severity === "error" ? "var(--bad)" : "var(--warn)" }}>
                      {iss.severity === "error" ? "✕" : "⚠"} {iss.message}
                    </div>
                  ))}
                </div>
              )}
              <button
                className={`copy-btn ${saved ? "copied" : ""}`}
                style={{ marginTop: 12 }}
                onClick={saveReview}
              >
                {saved ? "✓ Saved" : "Save reviewed values"}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
