// Pure helpers for the Intraday Hunter v2 UI (unit-tested in src/__tests__/ihV2.test.ts).

/** Position (0-100 %) of the MTM marker on a -T..+T bar; clamped, 50 when T is missing/zero. */
export function mtmBarPct(mtm: number | null | undefined, T: number | null | undefined): number {
  if (mtm == null || T == null || !(T > 0)) return 50;
  const pct = ((mtm + T) / (2 * T)) * 100;
  return Math.max(0, Math.min(100, pct));
}

/** Format a latency in ms as seconds, e.g. 15300 -> "15.3s"; sub-second stays in ms. */
export function formatLatency(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Tailwind classes for a gate verdict chip: AGREES green, OPPOSES amber, anything else grey. */
export function gateChipClass(verdict: string | null | undefined): string {
  if (verdict === "AGREES") return "bg-profit/15 text-profit";
  if (verdict === "OPPOSES") return "bg-warning/15 text-warning";
  return "bg-text-muted/20 text-text-muted";
}

/** Signed INR string with sign and thousands separators; "—" for null. */
export function formatPnl(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const r = Math.round(v);
  return `${r >= 0 ? "+" : "-"}${Math.abs(r).toLocaleString("en-IN")}`;
}

/** Basket header band label: "T ±15%" in pct mode, "T fixed ₹/lot" in rupees mode (each book's
 *  rupee T is shown on its own bar). */
export function basketTLabel(b: { basket_tp_sl_pct: number; basket_t_mode?: string } | null | undefined): string {
  if (!b) return "T —";
  if (b.basket_t_mode === "rupees") return "T fixed ₹/lot";
  return `T ±${(b.basket_tp_sl_pct * 100).toFixed(0)}%`;
}

/** Tailwind chip classes for a proposal status. */
export function proposalStatusClass(status: string | null | undefined): string {
  switch (status) {
    case "PROPOSED":
      return "bg-bg-tertiary text-text-secondary";
    case "APPROVED":
    case "ANALYSED":
      return "bg-accent/15 text-accent";
    case "APPLIED":
      return "bg-profit/15 text-profit";
    case "PROMOTED":
      return "bg-profit/30 text-profit font-semibold";
    case "NEEDS_REVIEW":
      return "bg-warning/15 text-warning";
    case "REJECTED":
    case "RETIRED":
      return "bg-text-muted/20 text-text-muted";
    default:
      return "bg-text-muted/20 text-text-muted";
  }
}

/** Button label for a proposal action. */
export function actionLabel(action: string): string {
  switch (action) {
    case "approve":
      return "Approve";
    case "reject":
      return "Reject";
    case "analyse":
      return "Re-analyse";
    case "promote":
      return "Promote";
    case "retire":
      return "Retire";
    default:
      return action;
  }
}

/** Whether an action needs the inline Confirm/Cancel step (approve starts an Opus agent; promote changes live params). */
export function needsConfirm(action: string): boolean {
  return action === "promote" || action === "retire" || action === "approve";
}

/** True while a background agent is working on the proposal (poll faster). */
export function isAgentPending(status: string | null | undefined): boolean {
  return status === "APPROVED";
}

/** Group proposals by week_ending, preserving input order. */
export function groupByWeek<T extends { week_ending: string }>(proposals: T[]): { week_ending: string; items: T[] }[] {
  const out: { week_ending: string; items: T[] }[] = [];
  for (const p of proposals) {
    const last = out[out.length - 1];
    const g = last && last.week_ending === p.week_ending ? last : out.find((x) => x.week_ending === p.week_ending);
    if (g) g.items.push(p);
    else out.push({ week_ending: p.week_ending, items: [p] });
  }
  return out;
}
