import "./globals.css";
import type { Metadata } from "next";
import { ChatWidget } from "./components/ChatWidget";
import { SessionProvider } from "./lib/session";

export const metadata: Metadata = {
  title: "ITR Assist - Automated Filing for Salaried Individuals",
  description:
    "AI document intelligence and deterministic tax computation for ITR filing (AY 2026-27).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <div className="container row">
            <div className="brand">
              <div className="logo">ƒ</div>
              <div>
                ITR Assist <small>· Salaried</small>
              </div>
            </div>
            <span className="tag-ay">AY 2026-27 · FY 2025-26</span>
          </div>
        </header>
        <SessionProvider>{children}</SessionProvider>
        <ChatWidget />
      </body>
    </html>
  );
}
