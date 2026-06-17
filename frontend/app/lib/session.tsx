"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

// Must mirror _MULTI_UPLOAD_TYPES in backend/app/routes.py
const MULTI_UPLOAD_TYPES = new Set(["form16", "form16a", "broker_pnl", "interest_cert", "donation_80g"]);
import { api } from "./api";
import type {
  ComputeStep,
  DocumentExtraction,
  ExtractedField,
  RegimeComparison,
  StreamEvent,
  TaxComputation,
} from "./types";

interface Activity {
  id: number;
  kind: "info" | "flag" | "verify" | "err";
  text: string;
}

interface LiveDoc {
  uploadId: string;
  docType: string;
  status: string;
  confidence: number;
  fields: Record<string, ExtractedField>;
  issues: { severity: string; message: string }[];
  extraction?: DocumentExtraction;
}

interface SessionState {
  sessionId: string | null;
  activity: Activity[];
  liveDocs: Record<string, LiveDoc>;
  extractions: Record<string, DocumentExtraction | DocumentExtraction[]>;
  reconFlags: { severity: string; message: string }[];
  computeSteps: ComputeStep[];
  computation: TaxComputation | null;
  comparison: RegimeComparison | null;
  verification: { verified: boolean; note: string } | null;
  busy: boolean;
  start: () => Promise<string>;
  restoreSession: (sid: string) => void;
  setBusy: (b: boolean) => void;
  setComputation: (c: TaxComputation) => void;
  setComparison: (c: RegimeComparison) => void;
  resetCompute: () => void;
}

const Ctx = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [liveDocs, setLiveDocs] = useState<Record<string, LiveDoc>>({});
  const [extractions, setExtractions] = useState<Record<string, DocumentExtraction | DocumentExtraction[]>>({});
  const [reconFlags, setReconFlags] = useState<{ severity: string; message: string }[]>([]);
  const [computeSteps, setComputeSteps] = useState<ComputeStep[]>([]);
  const [computation, setComputation] = useState<TaxComputation | null>(null);
  const [comparison, setComparison] = useState<RegimeComparison | null>(null);
  const [verification, setVerification] = useState<{ verified: boolean; note: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const pushActivity = (kind: Activity["kind"], text: string) => {
    if (!text) return;
    const id = Date.now() * 1000 + Math.floor(Math.random() * 1000);
    setActivity((a) => [{ id, kind, text }, ...a].slice(0, 60));
  };

  const handleEvent = (ev: StreamEvent) => {
    const d = ev.data || {};
    switch (ev.type) {
      case "agent.step":
      case "info":
        pushActivity("info", ev.message || "");
        break;
      case "doc.started": {
        pushActivity("info", ev.message || "");
        const key = d.upload_id || d.doc_type;
        setLiveDocs((s) => ({
          ...s,
          [key]: {
            uploadId: key, docType: d.doc_type, status: "extracting",
            confidence: 0, fields: {}, issues: [],
          },
        }));
        break;
      }
      case "doc.field.extracted": {
        const key = d.upload_id || d.doc_type;
        setLiveDocs((s) => {
          const doc = s[key] || {
            uploadId: key, docType: d.doc_type, status: "extracting",
            confidence: 0, fields: {}, issues: [],
          };
          return {
            ...s,
            [key]: {
              ...doc,
              fields: {
                ...doc.fields,
                [d.name]: {
                  name: d.name, label: d.label, value: d.value,
                  confidence: d.confidence, source_hint: d.source_hint, flagged: d.flagged,
                },
              },
            },
          };
        });
        break;
      }
      case "doc.validated": {
        pushActivity("info", ev.message || "");
        const key = d.upload_id || d.doc_type;
        setLiveDocs((s) => {
          const doc = s[key] || {
            uploadId: key, docType: d.doc_type, status: "",
            confidence: 0, fields: {}, issues: [],
          };
          return {
            ...s,
            [key]: { ...doc, status: d.status, confidence: d.confidence, issues: d.issues || [] },
          };
        });
        break;
      }
      case "doc.completed": {
        if (!d.extraction) break;
        const key = d.upload_id || d.doc_type;
        setLiveDocs((s) => {
          const doc = s[key] || {
            uploadId: key, docType: d.doc_type, status: d.extraction.status,
            confidence: d.extraction.overall_confidence, fields: {}, issues: [],
          };
          return { ...s, [key]: { ...doc, status: d.extraction.status, extraction: d.extraction } };
        });
        if (MULTI_UPLOAD_TYPES.has(d.doc_type)) {
          setExtractions((s) => {
            const prev = s[d.doc_type];
            const arr = Array.isArray(prev) ? prev : prev ? [prev] : [];
            return { ...s, [d.doc_type]: [...arr, d.extraction] };
          });
        } else {
          setExtractions((s) => ({ ...s, [d.doc_type]: d.extraction }));
        }
        break;
      }
      case "recon.flag":
        pushActivity("flag", ev.message || "");
        setReconFlags((f) => [...f, { severity: d.severity, message: ev.message || "" }]);
        break;
      case "recon.done":
        pushActivity("info", ev.message || "");
        break;
      case "compute.step":
        setComputeSteps((s) => [...s, { key: d.key, label: d.label, amount: d.amount, kind: d.kind, detail: d.detail || "" }]);
        break;
      case "verification":
        pushActivity("verify", ev.message || "");
        setVerification({ verified: !!d.verified, note: ev.message || "" });
        break;
      case "compute.done":
        pushActivity("verify", ev.message || "");
        if (d.computation) setComputation(d.computation);
        if (d.comparison) setComparison(d.comparison);
        break;
      case "error":
        pushActivity("err", ev.message || "Error");
        break;
    }
  };

  useEffect(() => {
    if (!sessionId) return;
    const es = new EventSource(api.streamUrl(sessionId));
    const types = [
      "agent.step", "info", "doc.started", "doc.field.extracted", "doc.validated",
      "doc.completed", "recon.flag", "recon.done", "compute.step", "verification",
      "compute.done", "error",
    ];
    const listener = (e: MessageEvent) => {
      try { handleEvent(JSON.parse(e.data)); } catch { /* ignore keepalive */ }
    };
    types.forEach((t) => es.addEventListener(t, listener as EventListener));
    return () => es.close();
  }, [sessionId]);

  const start = async () => {
    const { session_id } = await api.createSession();
    setSessionId(session_id);
    return session_id;
  };

  const restoreSession = (sid: string) => setSessionId(sid);

  const resetCompute = () => {
    setComputeSteps([]);
    setComputation(null);
    setComparison(null);
    setVerification(null);
    setReconFlags([]);
  };

  const value = useMemo<SessionState>(
    () => ({
      sessionId, activity, liveDocs, extractions, reconFlags, computeSteps,
      computation, comparison, verification, busy, start, restoreSession, setBusy,
      setComputation, setComparison, resetCompute,
    }),
    [sessionId, activity, liveDocs, extractions, reconFlags, computeSteps, computation, comparison, verification, busy] // eslint-disable-line react-hooks/exhaustive-deps
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSession must be used within SessionProvider");
  return ctx;
}
