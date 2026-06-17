"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { confClass, confLabel, docIcon, inr } from "../lib/format";
import { useSession } from "../lib/session";
import type { ChecklistItem, DocumentExtraction, ExtractedField } from "../lib/types";

// Must mirror _MULTI_UPLOAD_TYPES in backend/app/routes.py
const MULTI_UPLOAD_TYPES = new Set([
  "form16", "form16a", "broker_pnl", "interest_cert", "donation_80g",
]);

interface Props {
  checklist: ChecklistItem[];
  sessionId: string;
  onNext: () => void;
  onBack: () => void;
}

// One locally-tracked uploaded file. Live extraction data is merged in from the
// session's liveDocs (keyed by uploadId) as SSE events arrive.
interface LocalUpload {
  uploadId: string;
  docType: string;
  fileName: string;
  previewUrl: string;
  kind: "image" | "pdf" | "excel" | "other";
  locked?: boolean; // password-protected PDF — browser can't preview
  failed?: boolean;
}

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  extracting: "Extracting",
  extracted: "Extracted",
  needs_review: "Needs review",
  validated: "Validated",
  failed: "Failed",
};

function makeId() {
  return Math.random().toString(36).slice(2, 14);
}

export function Upload({ checklist, sessionId, onNext, onBack }: Props) {
  const { liveDocs } = useSession();
  const [uploads, setUploads] = useState<LocalUpload[]>([]);

  useEffect(() => {
    return () => uploads.forEach((u) => URL.revokeObjectURL(u.previewUrl));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addUpload = async (docType: string, file: File, password?: string) => {
    const uploadId = makeId();
    const previewUrl = URL.createObjectURL(file);
    const nameLower = file.name.toLowerCase();
    const kind = file.type.startsWith("image/")
      ? "image"
      : file.type === "application/pdf" || nameLower.endsWith(".pdf")
      ? "pdf"
      : nameLower.endsWith(".xlsx") || nameLower.endsWith(".xls") || nameLower.endsWith(".csv")
      ? "excel"
      : "other";
    const isMulti = MULTI_UPLOAD_TYPES.has(docType);
    setUploads((u) => {
      // Single-upload types: replace any prior local entry for that type.
      const base = isMulti ? u : u.filter((x) => x.docType !== docType);
      const locked = kind === "pdf" && !!password;
      return [...base, { uploadId, docType, fileName: file.name, previewUrl, kind, locked }];
    });
    try {
      await api.uploadDocument(sessionId, docType, file, password, uploadId);
    } catch {
      setUploads((u) =>
        u.map((x) => (x.uploadId === uploadId ? { ...x, failed: true } : x))
      );
    }
  };

  const uploadsByType = useMemo(() => {
    const map: Record<string, LocalUpload[]> = {};
    for (const u of uploads) (map[u.docType] ||= []).push(u);
    return map;
  }, [uploads]);

  const requiredItems = checklist.filter((c) => c.required);
  const requiredDone = requiredItems.filter((c) =>
    (uploadsByType[c.doc_type] || []).some((u) => liveDocs[u.uploadId]?.extraction)
  ).length;
  const anyUploaded = uploads.length > 0;

  return (
    <div className="card">
      <h2>Upload &amp; watch extraction</h2>
      <p className="sub">
        Drop one or more files into each section. Uploads start immediately and extract in
        parallel; you do not have to wait for one to finish before adding the next. Each file
        shows a live preview, every extracted field, and its confidence.
      </p>

      <div className="upload-progress">
        <div className="up-bar">
          <div
            className="up-fill"
            style={{ width: `${requiredItems.length ? (requiredDone / requiredItems.length) * 100 : 0}%` }}
          />
        </div>
        <span className="up-count">
          {requiredDone}/{requiredItems.length} required documents ready
        </span>
      </div>

      <div className="upload-sections">
        {checklist.map((item) => (
          <DocTypeSection
            key={item.doc_type}
            item={item}
            sessionId={sessionId}
            uploads={uploadsByType[item.doc_type] || []}
            onFiles={(files, password) =>
              files.forEach((f) => addUpload(item.doc_type, f, password))
            }
          />
        ))}
      </div>

      <div className="btn-row sticky-actions">
        <button className="btn ghost" onClick={onBack}>Back</button>
        <button className="btn" onClick={onNext} disabled={!anyUploaded}>
          Continue ({uploads.length} uploaded)
        </button>
      </div>
    </div>
  );
}

function DocTypeSection({
  item,
  sessionId,
  uploads,
  onFiles,
}: {
  item: ChecklistItem;
  sessionId: string;
  uploads: LocalUpload[];
  onFiles: (files: File[], password?: string) => void;
}) {
  const { liveDocs, extractions } = useSession();
  const isMulti = MULTI_UPLOAD_TYPES.has(item.doc_type);
  const needsPassword = item.doc_type === "ais";
  const [open, setOpen] = useState(item.required);
  // AIS PDFs are encrypted with PAN + DOB as ddmmyyyy (portal uses lowercase).
  const [pan, setPan] = useState("");
  const [dob, setDob] = useState(""); // yyyy-mm-dd from the date picker
  const aisPassword =
    needsPassword && pan.trim() && dob
      ? pan.trim() + dob.split("-").reverse().join("")
      : undefined;

  const live = uploads.map((u) => liveDocs[u.uploadId]);
  const doneCount = live.filter((l) => l?.extraction).length;
  const anyNeedsReview = live.some((l) => l?.extraction?.status === "needs_review");
  const aggStatus =
    uploads.length === 0
      ? ""
      : anyNeedsReview
      ? "needs_review"
      : doneCount === uploads.length
      ? "validated"
      : "extracting";

  // Index into the persisted extraction list (completion order) for review edits.
  const extList = Array.isArray(extractions[item.doc_type])
    ? (extractions[item.doc_type] as DocumentExtraction[])
    : extractions[item.doc_type]
    ? [extractions[item.doc_type] as DocumentExtraction]
    : [];

  const showDropzone = isMulti || uploads.length === 0;

  return (
    <div className={`doc-section ${open ? "open" : ""}`}>
      <button className="doc-section-head" onClick={() => setOpen((o) => !o)}>
        <span className="dsh-title">
          <span className="dsh-caret">{open ? "▾" : "▸"}</span>
          {docIcon(item.doc_type)} {item.title}
          <span className={`tag ${item.required ? "req" : "opt"}`}>
            {item.required ? "Required" : "Optional"}
          </span>
          {item.covered_by && (
            <span className="tag covered" title={`Data already extracted from: ${item.covered_by}`}>
              Covered by {item.covered_by}
            </span>
          )}
        </span>
        <span className="dsh-status">
          {uploads.length > 0 && (
            <span className="dsh-count">{uploads.length} file{uploads.length > 1 ? "s" : ""}</span>
          )}
          {aggStatus && <span className={`badge ${aggStatus}`}>{STATUS_LABEL[aggStatus]}</span>}
        </span>
      </button>

      {open && (
        <div className="doc-section-body">
          <p className="dsh-why">{item.why}</p>

          {uploads.map((u) => (
            <UploadTile
              key={u.uploadId}
              upload={u}
              live={liveDocs[u.uploadId]}
              sessionId={sessionId}
              reviewIndex={
                liveDocs[u.uploadId]?.extraction
                  ? Math.max(0, extList.indexOf(liveDocs[u.uploadId]!.extraction!))
                  : 0
              }
            />
          ))}

          {showDropzone && (
            <Dropzone
              multi={isMulti}
              required={item.required}
              source={item.source}
              needsPassword={needsPassword}
              pan={pan}
              dob={dob}
              onPan={setPan}
              onDob={setDob}
              hasUploads={uploads.length > 0}
              title={item.title}
              onFiles={(files) => onFiles(files, aisPassword)}
            />
          )}
        </div>
      )}
    </div>
  );
}

function PreviewModal({
  upload,
  onClose,
}: {
  upload: LocalUpload;
  onClose: () => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="preview-modal-overlay"
      onClick={onClose}
    >
      <div
        className="preview-modal-content"
        onClick={(e) => e.stopPropagation()}
      >
        <button className="preview-modal-close" onClick={onClose} aria-label="Close preview">
          ✕
        </button>
        <div className="preview-modal-title">{upload.fileName}</div>
        <div className="preview-modal-body">
          {upload.kind === "image" ? (
            <img src={upload.previewUrl} alt={upload.fileName} style={{ maxWidth: "100%", maxHeight: "80vh", objectFit: "contain" }} />
          ) : upload.kind === "pdf" && !upload.locked ? (
            <embed
              src={`${upload.previewUrl}#toolbar=1&navpanes=0`}
              type="application/pdf"
              style={{ width: "100%", height: "80vh" }}
            />
          ) : upload.kind === "excel" ? (
            <div className="ut-preview-none">
              <span style={{ fontSize: 48 }}>📊</span>
              <span style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 8 }}>
                {upload.fileName.toLowerCase().endsWith(".csv") ? "CSV" : "Excel"} file — no browser preview available
              </span>
            </div>
          ) : upload.locked ? (
            <div className="ut-preview-none">
              <span style={{ fontSize: 48 }}>🔒</span>
              <span style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 8 }}>Encrypted PDF — preview not available</span>
            </div>
          ) : (
            <div className="ut-preview-none">
              <span style={{ fontSize: 13, color: "var(--text-faint)" }}>No preview available</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FilePreviewModal({
  upload,
  onClose,
}: {
  upload: LocalUpload;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="preview-modal-overlay" onClick={onClose}>
      <div className="preview-modal" onClick={(e) => e.stopPropagation()}>
        <div className="preview-modal-header">
          <span className="preview-modal-title">{upload.fileName}</span>
          <button className="preview-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="preview-modal-body">
          {upload.kind === "image" ? (
            <img src={upload.previewUrl} alt={upload.fileName} />
          ) : upload.kind === "pdf" && !upload.locked ? (
            <embed src={`${upload.previewUrl}#toolbar=1&navpanes=0`} type="application/pdf" />
          ) : upload.kind === "excel" ? (
            <div className="preview-modal-none">
              <span style={{ fontSize: 48 }}>📊</span>
              <span>{upload.fileName.toLowerCase().endsWith(".csv") ? "CSV" : "Excel"} file — no browser preview available</span>
            </div>
          ) : upload.locked ? (
            <div className="preview-modal-none">
              <span style={{ fontSize: 48 }}>🔒</span>
              <span>Encrypted PDF — preview unavailable</span>
            </div>
          ) : (
            <div className="preview-modal-none">
              <span style={{ fontSize: 48 }}>📄</span>
              <span>No preview available</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function UploadTile({
  upload,
  live,
  sessionId,
  reviewIndex,
}: {
  upload: LocalUpload;
  live?: {
    status: string;
    confidence: number;
    fields: Record<string, ExtractedField>;
    issues: { severity: string; message: string }[];
    extraction?: DocumentExtraction;
  };
  sessionId: string;
  reviewIndex: number;
}) {
  const extraction = live?.extraction;
  const status = upload.failed ? "failed" : extraction?.status || live?.status || "queued";
  const [open, setOpen] = useState(true);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // Collapse automatically once a document is fully validated; expand if it
  // needs review so issues are visible.
  useEffect(() => {
    if (status === "validated") setOpen(false);
    else if (status === "needs_review") setOpen(true);
  }, [status]);

  const fields: ExtractedField[] = extraction
    ? extraction.fields
    : live
    ? Object.values(live.fields)
    : [];
  const confidence = extraction?.overall_confidence ?? live?.confidence ?? 0;

  const saveReview = async () => {
    if (Object.keys(edits).length === 0) return;
    await api.reviewDocument(sessionId, upload.docType, edits, reviewIndex);
    setSaved(true);
  };

  return (
    <>
    {previewing && <PreviewModal upload={upload} onClose={() => setPreviewing(false)} />}
    <div className={`upload-tile ${status}`}>
      <button className="ut-head" onClick={() => setOpen((o) => !o)}>
        <span className="ut-name">
          <span className="dsh-caret">{open ? "▾" : "▸"}</span>
          <span
            className="ut-filename-link"
            onClick={(e) => { e.stopPropagation(); setPreviewing(true); }}
            title="Click to preview"
          >
            {upload.fileName}
          </span>
        </span>
        <span className="ut-status">
          {extraction && (
            <span className={`conf-chip ${confClass(confidence)}`}>{confLabel(confidence)}</span>
          )}
          <span className={`badge ${status}`}>
            {STATUS_LABEL[status] || status}
            {status === "extracting" && <span className="spinner mini" />}
          </span>
        </span>
      </button>

      {open && (
        <div className="ut-body">
          {previewOpen && <FilePreviewModal upload={upload} onClose={() => setPreviewOpen(false)} />}
          <div
            className="ut-preview ut-preview-clickable"
            onClick={() => setPreviewOpen(true)}
            title="Click to enlarge preview"
          >
            {upload.kind === "image" ? (
              <img src={upload.previewUrl} alt={upload.fileName} />
            ) : upload.kind === "pdf" && !upload.locked ? (
              <embed src={`${upload.previewUrl}#toolbar=0&navpanes=0`} type="application/pdf" />
            ) : upload.kind === "excel" ? (
              <div className="ut-preview-none">
                <span style={{ fontSize: 28 }}>📊</span>
                <span style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 6 }}>
                  {upload.fileName.toLowerCase().endsWith(".csv") ? "CSV" : "Excel"} file
                </span>
              </div>
            ) : (
              <div className="ut-preview-none">
                {upload.locked ? (
                  <>
                    <span style={{ fontSize: 28 }}>🔒</span>
                    <span style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 6 }}>
                      Encrypted PDF
                    </span>
                  </>
                ) : (
                  docIcon(upload.docType)
                )}
              </div>
            )}
          </div>

          <div className="ut-fields">
            {upload.failed ? (
              <div className="guide-note" style={{ color: "var(--bad)" }}>
                ✕ Upload failed. Remove the section file and try again.
              </div>
            ) : fields.length === 0 ? (
              <div className="ut-waiting">
                <span className="spinner" style={{ borderTopColor: "var(--accent)" }} /> Reading
                document...
              </div>
            ) : (
              <>
                {/* Editable / computation fields */}
                {fields.filter(f => !f.display_only).map((f) => (
                  <div className="field-row" key={f.name}>
                    <span className="fl">
                      {f.label}
                      {f.source_hint && (
                        <span className="field-source-hint">{f.source_hint}</span>
                      )}
                    </span>
                    <span className="fv">
                      {extraction ? (
                        <input
                          className="field"
                          style={{ width: 120, textAlign: "right" }}
                          defaultValue={f.value ?? ""}
                          onChange={(e) => setEdits((s) => ({ ...s, [f.name]: e.target.value }))}
                        />
                      ) : (
                        <span>{typeof f.value === "number" ? inr(f.value) : f.value ?? "-"}</span>
                      )}
                      <span className={`conf-chip ${confClass(f.confidence)}`}>
                        {confLabel(f.confidence)}
                      </span>
                    </span>
                  </div>
                ))}

                {/* Display-only breakdown (informational, employer-certified) */}
                {fields.some(f => f.display_only && f.value != null && f.value !== 0) && (
                  <details className="breakdown-details" style={{ marginTop: 10 }}>
                    <summary style={{ cursor: "pointer", fontSize: 11, color: "var(--text-faint)", userSelect: "none" }}>
                      Allowance breakdown (employer-certified, informational only)
                    </summary>
                    <div style={{ marginTop: 6, paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                      <div style={{ fontSize: 10, color: "var(--text-faint)", marginBottom: 6 }}>
                        These amounts are already included in the Exempt Allowances total above and are used for verification only — they do not affect your tax.
                      </div>
                      {fields.filter(f => f.display_only && f.value != null && f.value !== 0).map((f) => (
                        <div className="field-row" key={f.name} style={{ opacity: 0.8 }}>
                          <span className="fl" style={{ fontSize: 12, color: "var(--text-faint)" }}>
                            {f.label.replace(/^\s+/, "")}
                            {f.source_hint && (
                              <span className="field-source-hint">{f.source_hint}</span>
                            )}
                          </span>
                          <span className="fv" style={{ fontSize: 12, color: "var(--text-faint)" }}>
                            <span>{typeof f.value === "number" ? inr(f.value) : f.value ?? "-"}</span>
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}

                {extraction && extraction.issues.length > 0 && (
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

                {extraction && (
                  <button
                    className={`copy-btn ${saved ? "copied" : ""}`}
                    style={{ marginTop: 12 }}
                    onClick={saveReview}
                  >
                    {saved ? "✓ Saved" : "Save reviewed values"}
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
    </>
  );
}

function Dropzone({
  multi,
  required,
  source,
  needsPassword,
  pan,
  dob,
  onPan,
  onDob,
  hasUploads,
  title,
  onFiles,
}: {
  multi: boolean;
  required: boolean;
  source: string;
  needsPassword: boolean;
  pan: string;
  dob: string;
  onPan: (v: string) => void;
  onDob: (v: string) => void;
  hasUploads: boolean;
  title: string;
  onFiles: (files: File[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    onFiles(Array.from(list));
  };

  return (
    <>
      {needsPassword && (
        <div className="ais-pass">
          <p className="ais-pass-hint">
            The AIS PDF is password-protected. Enter your PAN and date of birth and we&apos;ll
            unlock it automatically (password = PAN + DDMMYYYY).
          </p>
          <div className="ais-pass-row">
            <input
              className="field"
              placeholder="PAN (e.g. ABCDE1234F)"
              maxLength={10}
              value={pan}
              onChange={(e) => onPan(e.target.value.toUpperCase())}
            />
            <input
              className="field"
              type="date"
              aria-label="Date of birth"
              value={dob}
              onChange={(e) => onDob(e.target.value)}
            />
          </div>
        </div>
      )}
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
      >
        <div>
          {hasUploads && multi
            ? `+ Add another ${title}`
            : "Drag & drop or click to upload"}
        </div>
        <div className="hint">
          {required ? "Required" : "Optional"} · {source}
          {multi ? " · multiple files allowed" : ""}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.xlsx,.xls,.csv,image/*"
          multiple={multi}
          style={{ display: "none" }}
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
    </>
  );
}
