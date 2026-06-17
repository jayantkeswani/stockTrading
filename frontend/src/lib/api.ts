import type { S5WatchlistItem, S5AgentLogEntry, S5GlobalCues, S5MorningBriefing, S5DailyStats, S5SetupPerformance, IntradayBias } from "./types";

function getApiBase(): string {
  if (typeof window === "undefined") return "http://localhost:8080";
  const { hostname, port, protocol } = window.location;
  if (port === "3000") return `http://${hostname}:8080`;
  return `${protocol}//${hostname}`;
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
      if (body.detail) {
        if (typeof body.detail === "string") {
          detail = body.detail;
        } else if (Array.isArray(body.detail)) {
          detail = body.detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
        } else {
          detail = JSON.stringify(body.detail);
        }
      }
    } catch { /* no json body */ }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  // Health
  health: () => request<{ status: string; version: string; deployed_at: string; data_feed_ready: boolean; startup_error: string | null }>(`/api/v1/health`),

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
  getIntradayBias: (symbol = "NIFTY") =>
    request<IntradayBias | null>(
      `/api/v1/market/intraday-bias?symbol=${encodeURIComponent(symbol)}`
    ),
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

  // YOLO Profiles
  getYoloProfiles: () =>
    request<import("./types").YoloProfile[]>(`/api/v1/yolo-profiles`),
  createYoloProfile: (data: { name: string; profit_cap: number; min_confidence_for_execution?: number | null }) =>
    request<import("./types").YoloProfile>(`/api/v1/yolo-profiles`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateYoloProfile: (id: string, data: { name?: string; profit_cap?: number; is_active?: boolean; sort_order?: number; invalidation_persist?: number | null; invalidation_quorum?: boolean; invalidation_strong_only?: boolean; min_confidence_for_execution?: number | null; min_bias_strength?: string; strategies?: string[]; setups?: string[] }) =>
    request<import("./types").YoloProfile>(`/api/v1/yolo-profiles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteYoloProfile: (id: string) =>
    request(`/api/v1/yolo-profiles/${id}`, { method: "DELETE" }),

  // Positions
  getPositions: (opts?: { includeShadow?: boolean; yolo_profile_id?: string }) => {
    const params = new URLSearchParams();
    if (opts?.includeShadow) params.set("include_shadow", "true");
    if (opts?.yolo_profile_id) params.set("yolo_profile_id", opts.yolo_profile_id);
    const qs = params.toString();
    return request(`/api/v1/positions${qs ? `?${qs}` : ""}`);
  },
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
    yolo_profile_id?: string;
    strategy?: string;
    limit?: number;
    entry_since?: string;
    entry_until?: string;
    // Simulation filters (signal-level)
    min_confidence?: number;
    max_confidence?: number;
    ai_action?: string;
    instrument_type?: string;
    signal_type?: string;
    setup_type?: string;
    min_adr?: number;
    // Trade-level sizing filter
    min_lots?: number;
    max_lots?: number;
    exclude_permanent?: boolean;
  }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.source) query.set("source", params.source);
    if (params?.yolo_profile_id) query.set("yolo_profile_id", params.yolo_profile_id);
    if (params?.strategy) query.set("strategy", params.strategy);
    if (params?.limit) query.set("limit", params.limit.toString());
    if (params?.entry_since) query.set("entry_since", params.entry_since);
    if (params?.entry_until) query.set("entry_until", params.entry_until);
    if (params?.min_confidence != null) query.set("min_confidence", params.min_confidence.toString());
    if (params?.max_confidence != null) query.set("max_confidence", params.max_confidence.toString());
    if (params?.ai_action) query.set("ai_action", params.ai_action);
    if (params?.instrument_type) query.set("instrument_type", params.instrument_type);
    if (params?.signal_type) query.set("signal_type", params.signal_type);
    if (params?.setup_type) query.set("setup_type", params.setup_type);
    if (params?.min_adr != null) query.set("min_adr", params.min_adr.toString());
    if (params?.min_lots != null) query.set("min_lots", params.min_lots.toString());
    if (params?.max_lots != null) query.set("max_lots", params.max_lots.toString());
    if (params?.exclude_permanent) query.set("exclude_permanent", "true");
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
  getTradeSummary: (opts?: { source?: string; yolo_profile_id?: string; exclude_permanent?: boolean }) => {
    const query = new URLSearchParams();
    if (opts?.source) query.set("source", opts.source);
    if (opts?.yolo_profile_id) query.set("yolo_profile_id", opts.yolo_profile_id);
    if (opts?.exclude_permanent) query.set("exclude_permanent", "true");
    const qs = query.toString();
    return request(`/api/v1/trades/summary${qs ? `?${qs}` : ""}`);
  },
  // Signals
  getSignals: (params?: { status?: string; generated_since?: string; generated_until?: string; strategy?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.generated_since) query.set("generated_since", params.generated_since);
    if (params?.generated_until) query.set("generated_until", params.generated_until);
    if (params?.strategy) query.set("strategy", params.strategy);
    if (params?.limit) query.set("limit", params.limit.toString());
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
  getSignalHistory: (id: string) =>
    request<import("./types").SignalHistory[]>(`/api/v1/signals/${id}/history`),

  // Risk
  getRiskDashboard: (yolo_profile_id?: string) => {
    const qs = yolo_profile_id ? `?yolo_profile_id=${encodeURIComponent(yolo_profile_id)}` : "";
    return request(`/api/v1/risk/dashboard${qs}`);
  },

  // Agent
  getAgentStatus: () => request(`/api/v1/agent/status`),
  startAgent: () => request(`/api/v1/agent/start`, { method: "POST" }),
  stopAgent: () => request(`/api/v1/agent/stop`, { method: "POST" }),
  getAgentLogs: (opts?: { limit?: number; since?: string; until?: string }) => {
    const params = new URLSearchParams({ limit: String(opts?.limit ?? 100) });
    if (opts?.since) params.set("since", opts.since);
    if (opts?.until) params.set("until", opts.until);
    return request(`/api/v1/agent/logs?${params}`);
  },
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
    shadow_enabled: boolean;
    yolo_enabled: boolean;
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
      min_confidence_to_persist: number;
      min_confidence_for_shadow: number;
      min_confidence_for_execution: number;
      shadow_skip_permanent_watchlist: boolean;
      yolo_skip_permanent_watchlist: boolean;
      fill_model: string;
    }>(`/api/v1/settings/trading`),
  updateTradingSettings: (patch: {
    capital?: number;
    max_daily_drawdown_pct?: number;
    max_risk_per_trade_pct?: number;
    max_trades_per_day?: number;
    paper_trading?: boolean;
    autonomy_level?: string;
    min_confidence_to_persist?: number;
    min_confidence_for_shadow?: number;
    min_confidence_for_execution?: number;
    shadow_skip_permanent_watchlist?: boolean;
    yolo_skip_permanent_watchlist?: boolean;
    ai_overlay_enabled?: boolean;
    fill_model?: string;
  }) =>
    request<{
      capital: number;
      max_daily_drawdown_pct: number;
      max_risk_per_trade_pct: number;
      max_trades_per_day: number;
      paper_trading: boolean;
      autonomy_level: string;
      min_confidence_to_persist: number;
      min_confidence_for_shadow: number;
      min_confidence_for_execution: number;
      shadow_skip_permanent_watchlist: boolean;
      yolo_skip_permanent_watchlist: boolean;
      fill_model: string;
    }>(`/api/v1/settings/trading`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  // Strategy 5 — Intraday Futures
  getIntradayFuturesWatchlist: (date?: string) =>
    request<S5WatchlistItem[]>(`/api/v1/intraday-futures/watchlist${date ? `?date=${date}` : ""}`),

  getIntradayFuturesAgentLog: (date?: string, offset = 0, limit = 100) => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    params.set("offset", String(offset));
    params.set("limit", String(limit));
    return request<{ entries: S5AgentLogEntry[]; total: number }>(
      `/api/v1/intraday-futures/agent-log?${params.toString()}`
    );
  },

  // Options (Strategy 2 — VWAP Pullback)
  getOptionsWindowState: () =>
    request<{ window_state: string; market_open: boolean }>("/api/v1/options/window-state"),
  getOptionsAgentLog: (date?: string, offset = 0, limit = 100) => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    params.set("offset", String(offset));
    params.set("limit", String(limit));
    return request<{ entries: S5AgentLogEntry[]; total: number }>(
      `/api/v1/options/agent-log?${params.toString()}`
    );
  },

  getIntradayFuturesGlobalCues: (date?: string, force?: boolean) => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (force) params.set("force", "true");
    const qs = params.toString();
    return request<S5GlobalCues>(`/api/v1/intraday-futures/global-cues${qs ? `?${qs}` : ""}`);
  },

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

  getPermanentWatchlist: () =>
    request<{ symbols: string[] }>(`/api/v1/intraday-futures/permanent-watchlist`),

  addToPermanentWatchlist: (symbol: string) =>
    request<{ symbols: string[] }>(`/api/v1/intraday-futures/permanent-watchlist`, {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),

  removeFromPermanentWatchlist: (symbol: string) =>
    request<{ symbols: string[] }>(`/api/v1/intraday-futures/permanent-watchlist/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),

  // Hold Analysis
  holdAnalysis: (tradeIds: string[], scenario: "best" | "worst" | "eod" | "sl_tgt") =>
    request<import("./types").HoldAnalysisResponse>(`/api/v1/trades/hold-analysis`, {
      method: "POST",
      body: JSON.stringify({ trade_ids: tradeIds, scenario }),
    }),

  // Margin Analysis
  marginAnalysis: (tradeIds: string[]) =>
    request<{ peak_margin: number; peak_time: string | null; total_margin: number; trade_count: number }>(
      `/api/v1/trades/margin-analysis`,
      { method: "POST", body: JSON.stringify({ trade_ids: tradeIds }) }
    ),

};
