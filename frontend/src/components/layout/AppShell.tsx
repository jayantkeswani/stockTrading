"use client";

import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useIsMobile } from "@/hooks/useIsMobile";
import { MobileShell } from "@/components/mobile/MobileShell";

export function AppShell({ children }: { children: React.ReactNode }) {
  useWebSocket();
  const isMobile = useIsMobile();

  // Pre-mount: render a neutral background to avoid a hydration flash and to
  // keep desktop route effects from running on a phone for a frame.
  if (isMobile === null) return <div className="min-h-screen bg-bg-primary" />;

  // Mobile: a single tabbed shell (Signals / Positions / Watchlist / Trades).
  // The desktop route children are intentionally not rendered.
  if (isMobile) return <MobileShell />;

  return (
    <div className="min-h-screen bg-bg-primary noise-bg">
      <Sidebar />
      <Header />
      <main className="fixed top-9 left-12 right-0 bottom-0 p-3 overflow-y-auto">{children}</main>
    </div>
  );
}
