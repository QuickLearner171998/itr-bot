"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTIONS = [
  "ITR-1 or ITR-2 for me?",
  "How do I download my AIS?",
  "Old vs new regime - which is better?",
];

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: "assistant",
      content:
        "Hi! I can help with ITR filing, the income-tax portal, your documents, and how to use this site. What would you like to know?",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [streaming, setStreaming] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, open, status, streaming]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || sending) return;
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((m) => [...m, { role: "user", content: q }]);
    setInput("");
    setSending(true);
    setStatus("thinking");
    setStreaming(false);

    let started = false;
    const startAssistant = () => {
      if (started) return;
      started = true;
      setStatus("");
      setStreaming(true);
      setMessages((m) => [...m, { role: "assistant", content: "" }]);
    };
    const appendToLast = (text: string) =>
      setMessages((m) => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant") {
          copy[copy.length - 1] = { ...last, content: last.content + text };
        }
        return copy;
      });

    try {
      await api.chatStream(q, history, {
        onStatus: (s) => setStatus(s),
        onDelta: (t) => {
          startAssistant();
          appendToLast(t);
        },
        onError: (t) => {
          startAssistant();
          appendToLast(t);
        },
      });
      if (!started) {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: "Sorry, I could not generate a reply right now." },
        ]);
      }
    } catch {
      if (!started) {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: "Sorry, I hit an error. Please try again." },
        ]);
      }
    } finally {
      setSending(false);
      setStreaming(false);
      setStatus("");
    }
  };

  return (
    <>
      <button
        className="chat-fab"
        onClick={() => setOpen((o) => !o)}
        aria-label="Open help chat"
      >
        {open ? "✕" : "💬"}
      </button>

      {open && (
        <div className="chat-window">
          <div className="chat-header">
            <span className="pulse" /> ITR Assist Helper
          </div>
          <div className="chat-body" ref={bodyRef}>
            {messages.map((m, i) => {
              const isLast = i === messages.length - 1;
              return (
                <div key={i} className={`chat-msg ${m.role}`}>
                  {m.content}
                  {m.role === "assistant" && isLast && streaming && (
                    <span className="stream-cursor" />
                  )}
                </div>
              );
            })}
            {sending && status === "thinking" && (
              <div className="chat-msg assistant thinking">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
                <span className="thinking-label">Thinking...</span>
              </div>
            )}
            {messages.length <= 1 && (
              <div className="chat-suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="chat-input">
            <input
              className="field"
              placeholder="Ask about ITR, the portal, or this site..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send(input)}
            />
            <button className="btn" onClick={() => send(input)} disabled={sending}>
              Send
            </button>
          </div>
        </div>
      )}
    </>
  );
}
