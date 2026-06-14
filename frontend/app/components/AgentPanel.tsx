"use client";

import { useSession } from "../lib/session";

export function AgentPanel() {
  const { activity, verification } = useSession();

  return (
    <aside className="live-panel">
      <div className="card">
        <h3>
          <span className="pulse" /> Agent Activity
        </h3>
        {verification && (
          <div className={`verify-stamp ${verification.verified ? "ok" : "no"}`} style={{ marginBottom: 14 }}>
            {verification.verified ? "✓ Verified" : "✕ Needs review"}
          </div>
        )}
        <div className="activity">
          {activity.length === 0 ? (
            <div className="activity-empty">
              Live agent steps, extraction and computation events will appear here.
            </div>
          ) : (
            activity.map((a) => (
              <div key={a.id} className={`activity-item ${a.kind}`}>
                <span className="dot" />
                <span>{a.text}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
