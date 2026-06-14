export function inr(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "-";
  const n = typeof value === "string" ? parseFloat(value.replace(/,/g, "")) : value;
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(n);
}

export function confClass(c: number): string {
  if (c >= 0.85) return "conf-hi";
  if (c >= 0.6) return "conf-mid";
  return "conf-lo";
}

export function confLabel(c: number): string {
  return `${Math.round(c * 100)}%`;
}

const ICONS: Record<string, string> = {
  form16: "📄",
  form16a: "🧾",
  form26as: "🏛️",
  ais: "📊",
  broker_pnl: "📈",
  interest_cert: "🏦",
  home_loan_cert: "🏠",
  deduction_proof: "🧮",
};

export function docIcon(t: string): string {
  return ICONS[t] || "📁";
}
