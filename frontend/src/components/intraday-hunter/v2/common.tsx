"use client";

import type { ReactNode } from "react";

export const SECTION_HDR = "text-xs font-mono font-medium text-text-secondary uppercase tracking-wider";
export const CARD = "bg-bg-secondary border border-border rounded p-3 space-y-2";

/** Titled card wrapper shared by the v2 sections. */
export function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className={CARD}>
      <div className="flex items-center gap-2">
        <h2 className={SECTION_HDR}>{title}</h2>
        <div className="ml-auto flex items-center gap-2">{right}</div>
      </div>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-xs font-mono text-text-muted lowercase">{children}</p>;
}

/** Renders an arbitrary JSON value compactly (string / list / key-value) for LLM-shaped payloads. */
export function Val({ v }: { v: unknown }) {
  if (v == null || v === "") return <span className="text-text-muted">—</span>;
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return <span className="text-text-primary">{String(v)}</span>;
  }
  if (Array.isArray(v)) {
    return (
      <ul className="list-disc pl-4 space-y-0.5">
        {v.map((x, i) => (
          <li key={i}>
            <Val v={x} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="space-y-0.5">
      {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
        <div key={k} className="flex gap-1.5">
          <span className="text-text-muted shrink-0">{k}:</span>
          <div className="min-w-0">
            <Val v={x} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function pnlCls(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-text-secondary";
  return v > 0 ? "text-profit" : "text-loss";
}
