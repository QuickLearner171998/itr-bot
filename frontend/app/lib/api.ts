export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

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

  getQuestionnaire: () =>
    fetch(`${API_BASE}/api/questionnaire`).then(jsonOrThrow),

  submitIntake: (sid: string, answers: Record<string, any>) =>
    fetch(`${API_BASE}/api/session/${sid}/intake`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answers),
    }).then(jsonOrThrow),

  uploadDocument: (
    sid: string,
    docType: string,
    file: File,
    password?: string
  ) => {
    const form = new FormData();
    form.append("doc_type", docType);
    form.append("file", file);
    if (password) form.append("password", password);
    return fetch(`${API_BASE}/api/session/${sid}/documents`, {
      method: "POST",
      body: form,
    }).then(jsonOrThrow);
  },

  reviewDocument: (sid: string, docType: string, edits: Record<string, any>) =>
    fetch(`${API_BASE}/api/session/${sid}/documents/${docType}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(edits),
    }).then(jsonOrThrow),

  reconcile: (sid: string) =>
    fetch(`${API_BASE}/api/session/${sid}/reconcile`, { method: "POST" }).then(
      jsonOrThrow
    ),

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

  streamUrl: (sid: string) => `${API_BASE}/api/stream/${sid}`,
};
