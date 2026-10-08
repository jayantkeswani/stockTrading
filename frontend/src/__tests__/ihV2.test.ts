import { describe, it, expect } from "vitest";
import { mtmBarPct, formatLatency, gateChipClass, formatPnl } from "@/lib/ihV2";

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
