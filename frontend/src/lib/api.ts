import type { S5WatchlistItem, S5AgentLogEntry, S5GlobalCues, S5MorningBriefing, S5DailyStats, S5SetupPerformance } from "./types";

function getApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== "undefined" && window.location.hostname !== "localhost")
    return `http://${window.location.hostname}:8080`;
  return "http://localhost:8080";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* no json body */ }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  // Market
  getPrice: (symbol: string) => request(`/api/v1/market/price/${symbol}`),
  getAllPrices: () =>
    request<Record<string, { symbol: string; ltp: number; bid: number; ask: number; volume: number; change: number; change_pct: number; timestamp: string }>>(`/api/v1/market/prices`),
  refreshQuotes: () => request(`/api/v1/market/feed/refresh`, { method: "POST" }),
  getOHLCV: (symbol: string, { resolution, days }: { resolution?: string; days?: number } = {}) => {
    const params = new URLSearchParams();
    if (resolution) params.set("resolution", resolution);
    if (days) params.set("days", days.toString());
    return request<Array<{ timestamp: number; open: number; high: number; low: number; close: number; volume: number }>>(
      `/api/v1/market/ohlcv/${symbol}?${params}`
    );
  },
  getMarketStatus: () => request(`/api/v1/market/status`),
  searchSymbols: (query: string) =>
    request<{ results: Array<{ symbol: string; display: string; short_name: string; segment: string; strike: number; type: string; ltp: number; expiry: string; lot_size: number }> }>(
      `/api/v1/market/symbols/search?q=${encodeURIComponent(query)}`
    ),
  fetchBatchPrices: (symbols: string[]) =>
    request<Record<string, { symbol: string; ltp: number; bid: number; ask: number; volume: number; change: number; change_pct: number; timestamp: string }>>(
      `/api/v1/market/prices/batch`,
      { method: "POST", body: JSON.stringify({ symbols }) }
    ),
  startDataFeed: () => request(`/api/v1/market/feed/start`, { method: "POST" }),
  stopDataFeed: () => request(`/api/v1/market/feed/stop`, { method: "POST" }),
  getFyersStatus: () => request(`/api/v1/auth/fyers/status`),

  // Positions
  getPositions: (includeShadow = false) =>
    request(`/api/v1/positions${includeShadow ? "?include_shadow=true" : ""}`),
  closePosition: (id: string, reason = "MANUAL") =>
    request(`/api/v1/positions/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  updateSL: (id: string, stopLoss: number) =>
    request(`/api/v1/positions/${id}/sl`, {
      method: "PATCH",
      body: JSON.stringify({ stop_loss: stopLoss }),
    }),

  // Trades
  getTrades: (params?: {
    status?: string;
    source?: string;
    strategy?: string;
    limit?: number;
    entry_since?: string;
    entry_until?: string;
  }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.source) query.set("source", params.source);
    if (params?.strategy) query.set("strategy", params.strategy);
    if (params?.limit) query.set("limit", params.limit.toString());
    if (params?.entry_since) query.set("entry_since", params.entry_since);
    if (params?.entry_until) query.set("entry_until", params.entry_until);
    return request(`/api/v1/trades?${query}`);
  },
  getClosedTradesToday: (source?: string) => {
    // IST midnight = UTC midnight - 5h30m
    const now = new Date();
    const istOffset = 5 * 60 + 30; // minutes
    const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
    const istMs = utcMs + istOffset * 60000;
    const istToday = new Date(istMs);
    istToday.setHours(0, 0, 0, 0);
    const istMidnightUtc = new Date(istToday.getTime() - istOffset * 60000);
    const since = istMidnightUtc.toISOString();
    const query = new URLSearchParams({ status: "CLOSED", closed_since: since, limit: "100" });
    if (source) query.set("source", source);
    return request<import("./types").Trade[]>(`/api/v1/trades?${query}`);
  },
  getTradeSummary: (source?: string) => {
    const query = source ? `?source=${source}` : "";
    return request(`/api/v1/trades/summary${query}`);
  },
  closeTrade: (id: string, exitPrice?: number) =>
    request(`/api/v1/trades/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ exit_price: exitPrice, reason: "MANUAL" }),
    }),

  // Signals
  getSignals: (params?: { status?: string }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    return request(`/api/v1/signals?${query}`);
  },
  getActiveSignals: () => request(`/api/v1/signals/active`),
  previewSignal: (id: string) =>
    request<import("./types").SignalPreview>(`/api/v1/signals/${id}/preview`),
  executeSignal: (id: string, opts?: { lots?: number }) =>
    request(`/api/v1/signals/${id}/execute`, {
      method: "POST",
      body: opts ? JSON.stringify({ lots: opts.lots ?? null }) : undefined,
    }),
  rejectSignal: (id: string) =>
    request(`/api/v1/signals/${id}/reject`, { method: "POST" }),

  // Risk
  getRiskDashboard: () => request(`/api/v1/risk/dashboard`),

  // Agent
  getAgentStatus: () => request(`/api/v1/agent/status`),
  startAgent: () => request(`/api/v1/agent/start`, { method: "POST" }),
  stopAgent: () => request(`/api/v1/agent/stop`, { method: "POST" }),
  getAgentLogs: (limit = 50) => request(`/api/v1/agent/logs?limit=${limit}`),
  confirmAction: (logId: string, approved: boolean) =>
    request(`/api/v1/agent/confirm/${logId}`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  toggleYolo: (enabled: boolean) =>
    request(`/api/v1/agent/yolo`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),

  // Watchlist
  getWatchlist: () =>
    request<{ items: Array<{ symbol: string; display: string; segment: string; strike: number | null; option_type: string | null; expiry: string }> }>(
      `/api/v1/watchlist`
    ),
  addToWatchlist: (item: { symbol: string; display: string; segment?: string; strike?: number | null; option_type?: string | null; expiry?: string }) =>
    request(`/api/v1/watchlist`, {
      method: "POST",
      body: JSON.stringify(item),
    }),
  removeFromWatchlist: (symbol: string) =>
    request(`/api/v1/watchlist/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),

  // Strategies
  getStrategies: () => request<Array<{
    id: string;
    strategy_name: string;
    is_active: boolean;
    auto_mode: boolean;
    parameters: Record<string, unknown>;
    risk_params: Record<string, unknown>;
    symbols: string[];
    timeframes: string[];
  }>>(`/api/v1/strategies`),
  toggleStrategy: (name: string) =>
    request<{ strategy: string; is_active: boolean }>(`/api/v1/strategies/${name}/toggle`, { method: "PATCH" }),
  toggleAutoMode: (name: string) =>
    request<{ strategy: string; auto_mode: boolean }>(`/api/v1/strategies/${name}/auto-mode`, { method: "PATCH" }),
  updateStrategy: (name: string, body: Record<string, unknown>) =>
    request(`/api/v1/strategies/${name}`, { method: "PUT", body: JSON.stringify(body) }),
  getParameterDefaults: (name: string) =>
    request<Record<string, unknown>>(`/api/v1/strategies/${name}/parameter-defaults`),
  evaluateStrategy: (strategyName: string, symbol: string) =>
    request<{ symbol: string; strategy: string; signal_generated: boolean; signal_type: string | null; confidence: number | null }>(
      `/api/v1/strategies/evaluate`, { method: "POST", body: JSON.stringify({ strategy_name: strategyName, symbol }) }
    ),
  evaluateStrategyBatch: (strategyName: string) =>
    request<{ strategy: string; symbols_scanned: number; signals_generated: number; results: Array<{ symbol: string; signal_generated: boolean; signal_type: string | null; confidence: number | null }> }>(
      `/api/v1/strategies/evaluate/batch`, { method: "POST", body: JSON.stringify({ strategy_name: strategyName }) }
    ),

  // Tasks
  getTasks: () => request<{ tasks: import("./types").BackgroundTask[] }>(`/api/v1/tasks`),

  // Research
  startResearch: (symbol: string) =>
    request<{ report_id: string; symbol: string; display_name: string; status: string; agents_total: number }>(
      `/api/v1/research/start`,
      { method: "POST", body: JSON.stringify({ symbol }) }
    ),
  getResearchReports: (params?: { symbol?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.symbol) query.set("symbol", params.symbol);
    if (params?.limit) query.set("limit", params.limit.toString());
    return request<import("./types").ResearchReportListItem[]>(`/api/v1/research/reports?${query}`);
  },
  getResearchReport: (id: string) =>
    request<import("./types").ResearchReport>(`/api/v1/research/reports/${id}`),
  deleteResearchReport: (id: string) =>
    request(`/api/v1/research/reports/${id}`, { method: "DELETE" }),

  // Trading settings
  getTradingSettings: () =>
    request<{
      capital: number;
      max_daily_drawdown_pct: number;
      max_risk_per_trade_pct: number;
      max_trades_per_day: number;
      paper_trading: boolean;
      autonomy_level: string;
    }>(`/api/v1/settings/trading`),
  updateTradingSettings: (patch: {
    capital?: number;
    max_daily_drawdown_pct?: number;
    max_risk_per_trade_pct?: number;
    max_trades_per_day?: number;
    paper_trading?: boolean;
    autonomy_level?: string;
  }) =>
    request(`/api/v1/settings/trading`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  // Strategy 5 — Intraday Futures
  getIntradayFuturesWatchlist: (date?: string) =>
    request<S5WatchlistItem[]>(`/api/v1/intraday-futures/watchlist${date ? `?date=${date}` : ""}`),

  getIntradayFuturesAgentLog: (date?: string) =>
    request<S5AgentLogEntry[]>(`/api/v1/intraday-futures/agent-log${date ? `?date=${date}` : ""}`),

  getIntradayFuturesGlobalCues: (date?: string) =>
    request<S5GlobalCues>(`/api/v1/intraday-futures/global-cues${date ? `?date=${date}` : ""}`),

  getIntradayFuturesBriefing: (date?: string) =>
    request<S5MorningBriefing>(`/api/v1/intraday-futures/morning-briefing${date ? `?date=${date}` : ""}`),

  getIntradayFuturesDailyStats: (date?: string) =>
    request<S5DailyStats>(`/api/v1/intraday-futures/daily-stats${date ? `?date=${date}` : ""}`),

  getIntradayFuturesPhase: (date?: string) =>
    request<{ phase: string }>(`/api/v1/intraday-futures/phase${date ? `?date=${date}` : ""}`),

  getIntradayFuturesAgentStatus: () =>
    request<{ status: string }>(`/api/v1/intraday-futures/agent-status`),

  getIntradayFuturesSetupPerformance: (date?: string, days?: number) => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (days) params.set("days", String(days));
    const qs = params.toString();
    return request<S5SetupPerformance>(`/api/v1/intraday-futures/setup-performance${qs ? `?${qs}` : ""}`);
  },

  runIntradayFuturesScreener: () =>
    request<{ status: string; watchlist_count: number }>(`/api/v1/intraday-futures/screener/run`, { method: "POST" }),

  runIntradayFuturesBriefing: () =>
    request<{ status: string; briefing: Record<string, unknown> }>(`/api/v1/intraday-futures/briefing/run`, { method: "POST" }),

  setIntradayFuturesAgentAction: (action: "pause" | "resume") =>
    request<{ status: string }>(`/api/v1/intraday-futures/agent/${action}`, { method: "POST" }),

  // Health
  health: () => request(`/api/v1/health`),
};
