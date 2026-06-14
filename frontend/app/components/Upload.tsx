"use client";

import { useRef, useState } from "react";
import { api } from "../lib/api";
import { confClass, confLabel, docIcon, inr } from "../lib/format";
import { useSession } from "../lib/session";
import type { ChecklistItem, DocumentExtraction, ExtractedField } from "../lib/types";

// Must mirror _MULTI_UPLOAD_TYPES in backend/app/routes.py
const MULTI_UPLOAD_TYPES = new Set(["form16", "form16a", "broker_pnl", "interest_cert", "donation_80g"]);

interface Props {
  checklist: ChecklistItem[];
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

export function Upload({ checklist, sessionId, onNext, onBack }: Props) {
  const { extractions } = useSession();
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

function FieldList({
  fields,
  extraction,
  index,
  sessionId,
  docType,
}: {
  fields: ExtractedField[];
  extraction?: DocumentExtraction;
  index: number;
  sessionId: string;
  docType: string;
}) {
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);

  const saveReview = async () => {
    if (Object.keys(edits).length === 0) return;
    await api.reviewDocument(sessionId, docType, edits, index);
    setSaved(true);
  };

  return (
    <div>
      {fields.map((f) => (
        <div className="field-row" key={f.name}>
          <span className="fl" title={f.source_hint || ""}>{f.label}</span>
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
                <div
                  key={i}
                  className="guide-note"
                  style={{ color: iss.severity === "error" ? "var(--bad)" : "var(--warn)" }}
                >
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
  );
}

function Dropzone({
  uploading,
  required,
  source,
  password,
  onPassword,
  onFile,
  needsPassword,
  label,
}: {
  uploading: boolean;
  required: boolean;
  source: string;
  password: string;
  onPassword: (v: string) => void;
  onFile: (f: File) => void;
  needsPassword: boolean;
  label?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      {needsPassword && (
        <input
          className="field"
          style={{ width: "100%", marginBottom: 8 }}
          placeholder="AIS PDF password (PAN + DOB)"
          value={password}
          onChange={(e) => onPassword(e.target.value)}
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
            <div>{label ?? "Click to upload PDF or image"}</div>
            <div className="hint">{required ? "Required" : "Optional"} · {source}</div>
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
  );
}

function ExtractCard({ item, sessionId }: { item: ChecklistItem; sessionId: string }) {
  const { liveDocs, extractions } = useSession();
  const [uploading, setUploading] = useState(false);
  const [password, setPassword] = useState("");

  const isMulti = MULTI_UPLOAD_TYPES.has(item.doc_type);
  const raw = extractions[item.doc_type];
  const extractionList: DocumentExtraction[] = Array.isArray(raw)
    ? raw
    : raw
    ? [raw]
    : [];

  const live = liveDocs[item.doc_type];
  const needsPassword = item.doc_type === "ais";

  const onFile = async (file: File) => {
    setUploading(true);
    try {
      await api.uploadDocument(sessionId, item.doc_type, file, password || undefined);
    } finally {
      setUploading(false);
    }
  };

  const showDropzone = extractionList.length === 0 || isMulti;
  const lastStatus = extractionList[extractionList.length - 1]?.status ?? live?.status;

  return (
    <div className="extract-card">
      <div className="extract-head">
        <span className="name">
          {docIcon(item.doc_type)} {item.title}
          {isMulti && extractionList.length > 0 && (
            <span className="badge validated" style={{ marginLeft: 6 }}>
              {extractionList.length} uploaded
            </span>
          )}
        </span>
        {!isMulti && lastStatus && (
          <span className={`badge ${lastStatus}`}>{lastStatus.replace("_", " ")}</span>
        )}
      </div>

      {extractionList.map((ext, idx) => (
        <div key={idx} style={extractionList.length > 1 ? { borderTop: "1px solid var(--border)", paddingTop: 8, marginTop: 8 } : {}}>
          {extractionList.length > 1 && (
            <div className="hint" style={{ marginBottom: 4 }}>
              #{idx + 1} — {ext.fields.find((f) => f.name === "employer_name")?.value ?? ext.doc_type}
            </div>
          )}
          <FieldList
            fields={ext.fields}
            extraction={ext}
            index={idx}
            sessionId={sessionId}
            docType={item.doc_type}
          />
        </div>
      ))}

      {extractionList.length === 0 && live && (
        <FieldList
          fields={Object.values(live.fields)}
          index={0}
          sessionId={sessionId}
          docType={item.doc_type}
        />
      )}

      {showDropzone && (
        <Dropzone
          uploading={uploading}
          required={item.required}
          source={item.source}
          password={password}
          onPassword={setPassword}
          onFile={onFile}
          needsPassword={needsPassword}
          label={isMulti && extractionList.length > 0 ? `+ Add another ${item.title}` : undefined}
        />
      )}
    </div>
  );
}
