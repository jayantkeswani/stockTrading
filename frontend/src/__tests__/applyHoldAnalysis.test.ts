/**
 * Tests for applyHoldAnalysis — copied from trades/page.tsx (module-level, not exported).
 *
 * Function logic:
 *  - Only transforms CLOSED trades that have an exit_price.
 *  - Looks up the trade id in holdMap for {max_high, min_low}.
 *  - "best" scenario uses max_high; "worst" uses min_low.
 *  - BUY  pnl = (hypoExit - entry) * qty
 *  - SELL pnl = (entry - hypoExit) * qty
 *  - pnl_percent = (diff / entry) * 100
 *  - net_pnl is always set to null on a transformed trade.
 */

import { describe, it, expect } from "vitest";
import type { Trade } from "@/lib/types";

// ---------------------------------------------------------------------------
// Copied verbatim from frontend/src/app/trades/page.tsx
// ---------------------------------------------------------------------------
type HoldResultMap = Map<string, { max_high: number | null; min_low: number | null }>;

function applyHoldAnalysis(
  trade: Trade,
  holdMap: HoldResultMap,
  scenario: "best" | "worst"
): Trade {
  if (trade.status !== "CLOSED" || trade.exit_price == null) return trade;
  const result = holdMap.get(trade.id);
  if (!result) return trade;

  const hypoExit = scenario === "best" ? result.max_high : result.min_low;
  if (hypoExit == null) return trade;

  const entry = Number(trade.entry_price);
  const qty = Number(trade.quantity);
  if (entry === 0 || qty === 0) return trade;

  const diff = trade.side === "SELL" ? (entry - hypoExit) : (hypoExit - entry);
  const hypoPnl = diff * qty;
  const hypoPnlPct = (diff / entry) * 100;

  return {
    ...trade,
    pnl: hypoPnl,
    pnl_percent: hypoPnlPct,
    net_pnl: null,
  };
}
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------
function makeTrade(overrides: Partial<Trade> = {}): Trade {
  return {
    id: "trade-1",
    signal_id: null,
    strategy_name: "vwap_pullback",
    symbol: "NIFTY",
    expiry_date: "2026-05-29",
    strike_price: 24000,
    option_type: "CE",
    side: "BUY",
    quantity: 50,
    lots: 1,
    entry_price: 100,
    exit_price: 110,
    stop_loss: 70,
    target_price: 150,
    status: "CLOSED",
    exit_reason: "TARGET",
    is_paper: true,
    source: "MANUAL",
    pnl: 500,
    pnl_percent: 10,
    margin_required: null,
    charges_json: null,
    net_pnl: 490,
    is_permanent_watchlist: false,
    entry_time: "2026-05-23T04:00:00Z",
    exit_time: "2026-05-23T08:00:00Z",
    notes: null,
    created_at: "2026-05-23T04:00:00Z",
    signal_confidence: null,
    signal_ai_action: null,
    signal_ai_summary: null,
    signal_instrument_type: null,
    signal_type: null,
    signal_snapshot: null,
    signal_is_permanent_watchlist: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("applyHoldAnalysis", () => {
  it("best case BUY: max_high > entry_price → positive pnl and correct pnl_percent", () => {
    // entry=100, max_high=130, qty=50
    // diff = 130 - 100 = 30
    // pnl = 30 * 50 = 1500
    // pnl_percent = (30 / 100) * 100 = 30
    const trade = makeTrade({ entry_price: 100, quantity: 50 });
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: 130, min_low: 80 }],
    ]);

    const result = applyHoldAnalysis(trade, holdMap, "best");

    expect(result.pnl).toBe(1500);
    expect(result.pnl_percent).toBeCloseTo(30, 10);
  });

  it("worst case BUY: min_low < entry_price → negative pnl", () => {
    // entry=100, min_low=75, qty=50
    // diff = 75 - 100 = -25
    // pnl = -25 * 50 = -1250
    const trade = makeTrade({ entry_price: 100, quantity: 50 });
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: 130, min_low: 75 }],
    ]);

    const result = applyHoldAnalysis(trade, holdMap, "worst");

    expect(result.pnl).toBe(-1250);
    expect(result.pnl_percent).toBeCloseTo(-25, 10);
  });

  it("SELL side, best case: max_high > entry → negative pnl (short loses when price rises)", () => {
    // side=SELL, entry=100, max_high=120, qty=50
    // diff = entry - hypoExit = 100 - 120 = -20
    // pnl = -20 * 50 = -1000
    const trade = makeTrade({ side: "SELL", entry_price: 100, quantity: 50 });
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: 120, min_low: 80 }],
    ]);

    const result = applyHoldAnalysis(trade, holdMap, "best");

    expect(result.pnl).toBe(-1000);
    expect(result.pnl_percent).toBeCloseTo(-20, 10);
  });

  it("no result in holdMap → returns original trade unchanged", () => {
    const trade = makeTrade();
    const holdMap: HoldResultMap = new Map(); // empty — no entry for this trade

    const result = applyHoldAnalysis(trade, holdMap, "best");

    expect(result).toBe(trade); // same reference, not a copy
  });

  it("open trade (status=OPEN) → returns original trade unchanged", () => {
    const trade = makeTrade({ status: "OPEN", exit_price: null });
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: 130, min_low: 80 }],
    ]);

    const result = applyHoldAnalysis(trade, holdMap, "best");

    expect(result).toBe(trade);
  });

  it("hypoExit is null → returns original trade unchanged", () => {
    const trade = makeTrade();
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: null, min_low: null }],
    ]);

    const bestResult = applyHoldAnalysis(trade, holdMap, "best");
    const worstResult = applyHoldAnalysis(trade, holdMap, "worst");

    expect(bestResult).toBe(trade);
    expect(worstResult).toBe(trade);
  });

  it("net_pnl is set to null on transformed trade", () => {
    // Original trade has net_pnl=490; after transformation it must be null.
    const trade = makeTrade({ net_pnl: 490 });
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: 130, min_low: 80 }],
    ]);

    const result = applyHoldAnalysis(trade, holdMap, "best");

    expect(result.net_pnl).toBeNull();
  });

  it("pnl_percent formula: (diff / entry) * 100", () => {
    // entry=200, max_high=250, qty=25
    // diff = 250 - 200 = 50
    // pnl_percent = (50 / 200) * 100 = 25
    const trade = makeTrade({ entry_price: 200, quantity: 25 });
    const holdMap: HoldResultMap = new Map([
      ["trade-1", { max_high: 250, min_low: 160 }],
    ]);

    const result = applyHoldAnalysis(trade, holdMap, "best");

    expect(result.pnl_percent).toBeCloseTo(25, 10);
    // Also verify pnl independently: 50 * 25 = 1250
    expect(result.pnl).toBe(1250);
  });
});
