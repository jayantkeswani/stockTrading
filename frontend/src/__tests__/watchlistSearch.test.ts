import { describe, it, expect } from "vitest";
import { formatStrike, optionSearchQuery, matchesContract } from "@/lib/watchlistSearch";

describe("formatStrike", () => {
  it("drops the API's Decimal-string decimals", () => {
    expect(formatStrike("71900.00")).toBe("71900");
    expect(formatStrike(54800)).toBe("54800");
  });
  it("keeps a real fractional strike", () => expect(formatStrike("22350.50")).toBe("22350.5"));
  it("returns empty for junk", () => expect(formatStrike(undefined)).toBe(""));
});

describe("option search fallback", () => {
  it("builds the query without decimals (was 'SENSEX 71900.00CE')", () => {
    expect(optionSearchQuery("SENSEX", "71900.00", "CE")).toBe("SENSEX 71900CE");
    expect(optionSearchQuery("RELIANCE", 0, null)).toBe("RELIANCE");
  });
  it("matches the Fyers contract for a string strike", () => {
    expect(matchesContract("BSE:SENSEX26O1571900CE", "71900.00", "CE")).toBe(true);
    expect(matchesContract("BSE:SENSEX26O1571900PE", "71900.00", "CE")).toBe(false);
    expect(matchesContract("NSE:RELIANCE26OCTFUT", 0, null)).toBe(true);
  });
});
