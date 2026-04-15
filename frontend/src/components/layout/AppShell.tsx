"use client";

import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { useWebSocket } from "@/hooks/useWebSocket";

export function AppShell({ children }: { children: React.ReactNode }) {
  useWebSocket();

  return (
    <div className="min-h-screen bg-bg-primary">
      <Sidebar />
      <Header />
      <main className="ml-16 mt-12 p-4">{children}</main>
    </div>
  );
}
