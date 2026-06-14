"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "./api";
import type {
  ComputeStep,
  DocumentExtraction,
  ExtractedField,
  StreamEvent,
  TaxComputation,
} from "./types";

interface Activity {
  id: number;
  kind: "info" | "flag" | "verify" | "err";
  text: string;
}

interface LiveDoc {
  status: string;
  confidence: number;
  fields: Record<string, ExtractedField>;
  issues: { severity: string; message: string }[];
}

interface SessionState {
  sessionId: string | null;
  activity: Activity[];
  liveDocs: Record<string, LiveDoc>;
  extractions: Record<string, DocumentExtraction>;
  reconFlags: { severity: string; message: string }[];
  computeSteps: ComputeStep[];
  computation: TaxComputation | null;
  verification: { verified: boolean; note: string } | null;
  busy: boolean;
  start: () => Promise<string>;
  setBusy: (b: boolean) => void;
  setComputation: (c: TaxComputation) => void;
  resetCompute: () => void;
}

const Ctx = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [liveDocs, setLiveDocs] = useState<Record<string, LiveDoc>>({});
  const [extractions, setExtractions] = useState<Record<string, DocumentExtraction>>({});
  const [reconFlags, setReconFlags] = useState<{ severity: string; message: string }[]>([]);
  const [computeSteps, setComputeSteps] = useState<ComputeStep[]>([]);
  const [computation, setComputation] = useState<TaxComputation | null>(null);
  const [verification, setVerification] = useState<{ verified: boolean; note: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const counter = useRef(0);

  const pushActivity = (kind: Activity["kind"], text: string) => {
    if (!text) return;
    counter.current += 1;
    setActivity((a) => [{ id: counter.current, kind, text }, ...a].slice(0, 60));
  };

  const handleEvent = (ev: StreamEvent) => {
    const d = ev.data || {};
    switch (ev.type) {
      case "agent.step":
      case "info":
        pushActivity("info", ev.message || "");
        break;
      case "doc.started":
        pushActivity("info", ev.message || "");
        setLiveDocs((s) => ({
          ...s,
          [d.doc_type]: { status: "extracting", confidence: 0, fields: {}, issues: [] },
        }));
        break;
      case "doc.field.extracted":
        setLiveDocs((s) => {
          const doc = s[d.doc_type] || { status: "extracting", confidence: 0, fields: {}, issues: [] };
          return {
            ...s,
            [d.doc_type]: {
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
      case "doc.validated":
        pushActivity("info", ev.message || "");
        setLiveDocs((s) => {
          const doc = s[d.doc_type] || { status: "", confidence: 0, fields: {}, issues: [] };
          return { ...s, [d.doc_type]: { ...doc, status: d.status, confidence: d.confidence, issues: d.issues || [] } };
        });
        break;
      case "doc.completed":
        if (d.extraction) setExtractions((s) => ({ ...s, [d.doc_type]: d.extraction }));
        break;
      case "recon.flag":
        pushActivity("flag", ev.message || "");
        setReconFlags((f) => [...f, { severity: d.severity, message: ev.message || "" }]);
        break;
      case "recon.done":
        pushActivity("info", ev.message || "");
        break;
      case "compute.step":
        setComputeSteps((s) => [...s, { key: d.key, label: d.label, amount: d.amount, kind: d.kind }]);
        break;
      case "verification":
        pushActivity("verify", ev.message || "");
        setVerification({ verified: !!d.verified, note: ev.message || "" });
        break;
      case "compute.done":
        pushActivity("verify", ev.message || "");
        if (d.computation) setComputation(d.computation);
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

  const resetCompute = () => {
    setComputeSteps([]);
    setComputation(null);
    setVerification(null);
    setReconFlags([]);
  };

  const value = useMemo<SessionState>(
    () => ({
      sessionId, activity, liveDocs, extractions, reconFlags, computeSteps,
      computation, verification, busy, start, setBusy, setComputation, resetCompute,
    }),
    [sessionId, activity, liveDocs, extractions, reconFlags, computeSteps, computation, verification, busy]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSession must be used within SessionProvider");
  return ctx;
}
