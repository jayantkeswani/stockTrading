"use client";

// Shared status/decision/direction badge styling for the Intraday Hunter page.

const STATUS_CLS: Record<string, string> = {
  PENDING: "bg-text-muted/20 text-text-muted",
  THESIS_READY: "bg-accent/20 text-accent",
  WATCHING: "bg-warning/20 text-warning",
  ENTER: "bg-profit/20 text-profit",
  WAIT: "bg-warning/20 text-warning",
  SKIP: "bg-text-muted/20 text-text-secondary",
};

export function StatusChip({ status }: { status: string }) {
  const cls = STATUS_CLS[status] ?? STATUS_CLS.PENDING;
  return (
    <span className={`text-[10px] font-mono px-1.5 py-px rounded uppercase tracking-wider ${cls}`}>
      {status}
    </span>
  );
}

export function DirectionBadge({ direction }: { direction: string | null | undefined }) {
  if (!direction) return null;
  const cls = direction === "CE" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss";
  return (
    <span className={`text-[10px] font-mono px-1.5 py-px rounded font-medium ${cls}`}>
      {direction}
    </span>
  );
}

export function confidenceColor(conf: number | null | undefined): string {
  if (conf == null) return "text-text-muted";
  if (conf >= 70) return "text-profit";
  if (conf >= 50) return "text-accent";
  return "text-text-secondary";
}
