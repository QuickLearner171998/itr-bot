"use client";

export const STEPS = [
  "Start",
  "Upload",
  "Confirm",
  "Review",
  "Compute",
  "File",
];

export function Stepper({ current }: { current: number }) {
  return (
    <div className="stepper">
      {STEPS.map((label, i) => (
        <div
          key={label}
          className={`step-pill ${i === current ? "active" : ""} ${i < current ? "done" : ""}`}
        >
          <span className="n">{i < current ? "✓" : i + 1}</span>
          {label}
        </div>
      ))}
    </div>
  );
}
