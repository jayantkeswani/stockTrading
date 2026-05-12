"use client";

import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { useWebSocket } from "@/hooks/useWebSocket";

export function AppShell({ children }: { children: React.ReactNode }) {
  useWebSocket();

  return (
    <div className="min-h-screen bg-bg-primary noise-bg">
      <Sidebar />
      <Header />
      <main className="fixed top-9 left-12 right-0 bottom-0 p-3 overflow-y-auto">{children}</main>
    </div>
  );
}
