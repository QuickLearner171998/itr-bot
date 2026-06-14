export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "";

async function jsonOrThrow(res: Response) {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  createSession: () =>
    fetch(`${API_BASE}/api/session`, { method: "POST" }).then(jsonOrThrow),

  getState: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/state`).then(jsonOrThrow),

  getBaseChecklist: () =>
    fetch(`${API_BASE}/api/base-checklist`).then(jsonOrThrow),

  analyzeGaps: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/analyze-gaps`, { method: "POST" }).then(jsonOrThrow),

  submitGaps: (sid: string, answers: Record<string, any>) =>
    fetch(`${API_BASE}/api/session/${sid}/submit-gaps`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answers),
    }).then(jsonOrThrow),

  uploadDocument: (
    sid: string,
    docType: string,
    file: File,
    password?: string,
    uploadId?: string
  ) => {
    const form = new FormData();
    form.append("doc_type", docType);
    form.append("file", file);
    if (password) form.append("password", password);
    if (uploadId) form.append("upload_id", uploadId);
    return fetch(`${API_BASE}/api/session/${sid}/documents`, {
      method: "POST",
      body: form,
    }).then(jsonOrThrow);
  },

  reviewDocument: (sid: string, docType: string, edits: Record<string, any>, index = 0) =>
    fetch(`${API_BASE}/api/session/${sid}/documents/${docType}/review?index=${index}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(edits),
    }).then(jsonOrThrow),

  reconcile: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/reconcile`, { method: "POST" }).then(
      jsonOrThrow
    ),

  consolidatePreview: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/consolidate`, { method: "POST" }).then(
      jsonOrThrow
    ),

  saveTaxInput: (sid: string, taxInput: Record<string, any>) =>
    fetch(`${API_BASE}/api/session/${sid}/tax-input`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(taxInput),
    }).then(jsonOrThrow),

  compute: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/compute`, { method: "POST" }).then(
      jsonOrThrow
    ),

  guidance: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/guidance`).then(jsonOrThrow),

  chat: (message: string, history: { role: string; content: string }[]) =>
    fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
    }).then(jsonOrThrow),

  chatStream: async (
    message: string,
    history: { role: string; content: string }[],
    handlers: {
      onStatus?: (state: string) => void;
      onDelta?: (text: string) => void;
      onError?: (text: string) => void;
    }
  ): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
    });
    if (!res.ok || !res.body) {
      throw new Error(`${res.status}: chat stream failed`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE allows both \n and \r\n line endings; normalise to \n.
      const normalised = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      const frames = normalised.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data = line.slice(5).trim();
        }
        if (!data) continue;
        let parsed: any = {};
        try {
          parsed = JSON.parse(data);
        } catch {
          continue;
        }
        if (event === "status") handlers.onStatus?.(parsed.state);
        else if (event === "delta") handlers.onDelta?.(parsed.text || "");
        else if (event === "error") handlers.onError?.(parsed.text || "");
      }
    }
  },

  streamUrl: (sid: string) => `${API_BASE}/api/stream/${sid}`,
};
