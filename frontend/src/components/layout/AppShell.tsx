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
      <main className="ml-12 mt-9 p-3">{children}</main>
    </div>
  );
}
