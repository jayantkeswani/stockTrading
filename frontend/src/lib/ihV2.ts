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
