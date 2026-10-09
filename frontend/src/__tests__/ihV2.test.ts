import { describe, it, expect } from "vitest";
import { mtmBarPct, formatLatency, gateChipClass, formatPnl, basketTLabel } from "@/lib/ihV2";

describe("mtmBarPct", () => {
  it("centres at 0 MTM", () => expect(mtmBarPct(0, 100)).toBe(50));
  it("maps -T / +T to the ends", () => {
    expect(mtmBarPct(-100, 100)).toBe(0);
    expect(mtmBarPct(100, 100)).toBe(100);
  });
  it("clamps beyond +-T", () => {
    expect(mtmBarPct(500, 100)).toBe(100);
    expect(mtmBarPct(-500, 100)).toBe(0);
  });
  it("falls back to centre when T missing", () => {
    expect(mtmBarPct(10, null)).toBe(50);
    expect(mtmBarPct(10, 0)).toBe(50);
    expect(mtmBarPct(null, 100)).toBe(50);
  });
});

describe("formatLatency", () => {
  it("formats seconds", () => expect(formatLatency(15300)).toBe("15.3s"));
  it("formats sub-second", () => expect(formatLatency(420)).toBe("420ms"));
  it("handles null", () => expect(formatLatency(null)).toBe("—"));
});

describe("gateChipClass", () => {
  it("colours verdicts", () => {
    expect(gateChipClass("AGREES")).toContain("profit");
    expect(gateChipClass("OPPOSES")).toContain("warning");
    expect(gateChipClass("NA")).toContain("text-muted");
    expect(gateChipClass(undefined)).toContain("text-muted");
  });
});

describe("formatPnl", () => {
  it("signs values", () => {
    expect(formatPnl(1500)).toBe("+1,500");
    expect(formatPnl(-250.4)).toBe("-250");
    expect(formatPnl(null)).toBe("—");
  });
});

describe("basketTLabel", () => {
  it("pct mode shows the percent band (default when mode absent)", () => {
    expect(basketTLabel({ basket_tp_sl_pct: 0.15 })).toBe("T ±15%");
    expect(basketTLabel({ basket_tp_sl_pct: 0.2, basket_t_mode: "pct" })).toBe("T ±20%");
  });
  it("rupees mode says fixed per lot", () => {
    expect(basketTLabel({ basket_tp_sl_pct: 0.15, basket_t_mode: "rupees" })).toBe("T fixed ₹/lot");
  });
  it("handles a missing payload", () => expect(basketTLabel(null)).toBe("T —"));
});

import { proposalStatusClass, actionLabel, needsConfirm, isAgentPending, groupByWeek } from "@/lib/ihV2";

describe("proposal helpers", () => {
  it("status classes", () => {
    expect(proposalStatusClass("APPLIED")).toContain("text-profit");
    expect(proposalStatusClass("PROMOTED")).toContain("font-semibold");
    expect(proposalStatusClass("NEEDS_REVIEW")).toContain("text-warning");
    expect(proposalStatusClass("REJECTED")).toContain("text-text-muted");
    expect(proposalStatusClass("APPROVED")).toContain("text-accent");
    expect(proposalStatusClass("WAT")).toContain("text-text-muted");
  });
  it("action labels", () => {
    expect(actionLabel("analyse")).toBe("Re-analyse");
    expect(actionLabel("approve")).toBe("Approve");
    expect(actionLabel("promote")).toBe("Promote");
  });
  it("needsConfirm", () => {
    expect(needsConfirm("promote")).toBe(true);
    expect(needsConfirm("retire")).toBe(true);
    expect(needsConfirm("approve")).toBe(true);
    expect(needsConfirm("reject")).toBe(false);
    expect(needsConfirm("analyse")).toBe(false);
  });
  it("isAgentPending", () => {
    expect(isAgentPending("APPROVED")).toBe(true);
    expect(isAgentPending("ANALYSED")).toBe(false);
  });
  it("groupByWeek preserves order", () => {
    const g = groupByWeek([
      { week_ending: "2026-10-03", n: 1 },
      { week_ending: "2026-10-03", n: 2 },
      { week_ending: "2026-09-26", n: 3 },
    ]);
    expect(g.map((x) => x.week_ending)).toEqual(["2026-10-03", "2026-09-26"]);
    expect(g[0].items.map((i) => i.n)).toEqual([1, 2]);
    expect(groupByWeek([])).toEqual([]);
  });
});
