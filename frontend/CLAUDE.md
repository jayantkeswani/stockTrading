# Frontend - Next.js / React / TypeScript

## Tech Stack
- Next.js 15 (App Router)
- TypeScript (strict mode)
- Tailwind CSS v4 (dark theme only — institutional terminal aesthetic)
- Zustand for state management
- TradingView lightweight-charts for price charts
- WebSocket for real-time updates from backend on port 8080
- **Vitest** — unit test runner (`npm test` / `npm run test:watch`). Config: `vitest.config.ts` (node environment, `@/*` alias wired). Tests live in `src/__tests__/`.

## Module Map

### `src/app/` - Pages (App Router, 10 pages)
All pages use `'use client'` directive.

- `layout.tsx` — Root layout with AppShell wrapper
- `page.tsx` — Dashboard: sticky PnLStrip at top, 8-col left (ScannerHeader, ScannerPanel, ActivePositions, FuturesWatchlist) + 4-col right (Watchlist, ScanFeed, AgentFeed). ChartModal overlay. Fetches YOLO profiles on mount alongside other dashboard data.
- `trades/page.tsx` — Kite-style P&L dashboard. Filter state (period, strategy, Shadow/Real, sim panel) lives in Zustand store, persisted via `persist` middleware. SummaryStrip, PnLHeatmap, TradesTable, Margin Analysis section. Source pills `[Manual | <profile1> | … | Shadow]` driven by `yoloProfiles` store — fetched on mount. Active pill drives data fetch: `source: "MANUAL"`, `yolo_profile_id: <uuid>`, or `source: "SHADOW"`. Net P&L toggle, "+ Open" toggle, "− Pinned" toggle. Simulation filter panel: min confidence, AI action, simulate lots, instrument type, signal type — all applied client-side via `applySimLots()` (scales P&L, charges, and `margin_required` by lot ratio). **Hold Analysis panel**: "Hold ▲/▾" toggle (amber when open). Panel has Scenario pills (Best / Worst / EOD / SL/TGT). On open/scenario change, fetches `POST /trades/hold-analysis` for all closed trade IDs; backend returns per-trade `hold_exit_price`, `hold_pnl`, `hold_net_pnl`, `hold_charges_json`, `hold_exit_time`, and `hold_outcome` from `market_data_1m` (window: entry_time → hold cutoff, where cutoff = min(next same-instrument same-side re-entry, 15:30 IST)). Best=max high, Worst=min low, EOD=last candle close, SL/TGT=first SL or TGT hit (fallback to EOD close). `hold_outcome` only populated for `sl_tgt` scenario. Computes `holdDataMap` (keyed by trade ID) for extra table columns. Passes `holdScenario` to TradesTable for conditional Outcome column. Pipeline: `trades → filteredTrades → simTrades → {SummaryStrip, dailyPnL→PnLHeatmap, displayedTrades→TradesTable(+holdDataMap, holdScenario)}`. Second `SummaryStrip` with `label="HOLD"` renders below when hold is active and respects `showNetPnL` toggle via `holdDataMap` directly. TradesTable shows "Hold Exit", "Hold P&L", "Hold Exit Time / Exit Time" columns alongside actual values, plus "Outcome" column only when `sl_tgt` scenario is active.
- `signals/page.tsx` — Signal history feed. PeriodFilter + strategy pills + min-confidence slider + symbol/reason search bar. All filters persisted. `ConfidenceFactorsBar` uses strategy-aware label maps. `SignalCard` includes `SignalHistoryPanel`.
- `settings/page.tsx` — Strategy config (is_active, auto_mode, shadow_enabled, yolo_enabled, symbols with autocomplete + group presets), risk params, StrategyParams collapsible numeric inputs, "Skip Pinned Signals" row with Shadow/YOLO toggles. Save validation includes confidence tier ordering check. **YOLO Profiles section**: CRUD management for profiles (name, profit cap, active toggle). Profile changes call the YOLO profile API directly (no page-level save needed). CRUD operations sync to Zustand store (`setYoloProfiles`) so Dashboard/Trades pills update without page reload. Uses `committedProfilesRef` to track API-known values for onBlur dirty-checking.
- `research/page.tsx` — AI Research: symbol search, real-time agent progress, full report with expandable cards, past reports history.
- `agent/page.tsx` — Agent dashboard: YOLO toggle (disabled + grey when agent stopped), 6-card status grid (Mode card shows "OFF" when stopped, "YOLO"/"SEMI" when running; Profit Cap card shows per-profile status from `risk.profiles[]`), activity log. PeriodFilter, action-type pills, strategy pills, All/Real/Shadow filter, symbol text input, "Pending" checkbox. Toggle handlers re-fetch full status from API after mutation (no optimistic spread).
- `chart/page.tsx` — Full TradingView chart page. Symbol tab row (all 5 indices + watchlist items).
- `options/page.tsx` — Strategy 2 (VWAP Pullback) dedicated page. DayStatusBar-style header + window state badge. AgentLog in col-span-8.
- `intraday-futures/page.tsx` — Strategy 5 dedicated page. Fixed DayStatusBar, fixed-height two-column grid (`h-[calc(100vh-96px)]`) with per-column `overflow-y-auto`. 8-col left (Watchlist + PermanentWatchlist), 4-col right (AgentLog, SetupPerformance, GlobalCues). Config managed via Settings page. ChartModal overlay pattern.

### `src/components/` - React Components (by domain)

**layout/**
- `AppShell.tsx` — Main wrapper: sidebar (48px, fixed) + header (36px, fixed) + content area. `<main>` is `fixed top-9 left-12 right-0 bottom-0` with `overflow-y-auto` — makes it the scroll container so `sticky` elements work. Applies `noise-bg` texture.
- `Header.tsx` — Thin status bar (36px): IST clock, market status, VIX, Tasks/Fyers/Agent/WS indicators, "STARTING…" pulse when `data_feed_ready=false` (polls health every 3s until ready). Polls `getAllPrices()` + market status every 10s. Uses `useShallow` selector (no price-tick re-renders). `BiasIndicator` sub-component uses scoped `(s) => s.intradayBias` selector.
- `TasksPopup.tsx` — Background tasks popup (click-to-open). Shows all registered tasks with status dots, type badges, schedule/description, timestamps, errors. Polls every 5s while open.
- `AgentPopup.tsx` — Agent control popup: start/stop, SEMI/YOLO toggle, positions monitored, pending confirmations, uptime. Profit Cap section shows per-profile cap status with current P&L vs cap for each active profile. Reads Zustand store, writes via API. Click-outside to close.
- `Sidebar.tsx` — Narrow icon rail (48px): Dashboard → Futures (trending-up) → Options (bar chart) → Research → Trades → Signals (zap) → Agent → Settings.

**charts/**
- `PriceChart.tsx` — TradingView candlestick chart (1m/5m/15m/1h/1D). Active timeframe stored in Zustand. Refresh button re-fetches without recreating chart. Live updates via scoped `(s) => s.prices[s.selectedSymbol]` selector (re-renders only on selected symbol's price change, not all ticks). IST display via `localization.timeFormatter` adding `IST_OFFSET` (19800s) for axis labels only — backend timestamps fed as UTC unix seconds, do NOT shift them. `fitContent()` always called after data load; `rightOffset: 5` reserves space. OHLC legend via `subscribeCrosshairMove`. Three-effect architecture: create/destroy chart, load data, live ticks.
- `ChartModal.tsx` — Modal (90vw × 85vh) for detailed chart view. Symbol tabs: 5 default indices + custom watchlist items (fetched on open). Pop-out opens `/chart?symbol=…` in a new tab.

**dashboard/**
- `PnLCard.tsx` — Single-line strip: Day P&L with inline U/R breakdown, drawdown bar, trades count, NOTIONAL/RISK/MARGIN metrics. Filters by `positionsMinConfidence` from store. Profile-aware: filters positions and closed trades by active profile/manual mode based on `dashboardViewMode`. Shadow mode: computes from `shadowPositions` + `shadowClosedToday`. Prices via `(s) => s.prices` selector; non-price fields via `useShallow`.
- `Watchlist.tsx` — Uniform symbol list (indices + custom items). Search via `<SymbolSearchInput>`. Custom items stored via `/api/v1/watchlist`. Max-height 300px with scroll.
- `ScannerHeader.tsx` — Ultra-compact strategy pill bar. Triggers manual batch evaluation via `POST /api/v1/strategies/evaluate/batch`. Logs start/end to ScanFeed. Uses `(s) => s.addScanLog` selector (no price-tick re-renders).
- `ScannerPanel.tsx` — Structured signal cards with strategy-aware rendering. "+ Executed" toggle, symbol search, confidence filter. Uses `useShallow` for signal state (no price-tick re-renders). Three card sections: header (direction, symbol, `P` badge, timestamp, WindowBadge, confidence, strategy badge), prices (entry/SL/target/R:R + strategy context), actions (EXEC, Watch, Details expander, AI panel, dismiss). Details and AI panels mutually exclusive. AI button shows `✦ AI` when `ai_summary` present.
- `ExecuteSignalModal.tsx` — Pre-trade confirm modal. Fetches `GET /signals/{id}/preview` for live entry price + computed lots. Shows lot stepper, quantity, Notional, Margin, `preview.warnings`. Submits `POST /signals/{id}/execute` with optional lots override.
- `QuickStats.tsx` — Compact stats card: Trades Today (vs max), Open Positions count, Notional. Reads `risk` + `positions` via `useShallow` (no price-tick re-renders). Shows HALTED banner when `risk.is_halted`.
- `SymbolSelector.tsx` — Tab row for selecting the active index symbol. One button per SYMBOL (5 indices). Shows LTP + change % from store `prices`. Prices via `(s) => s.prices` selector; selection state via `useShallow`. Active button styled with amber accent.
- `ScanFeed.tsx` — Compact scan log, max-height 160px. Filters `scanLogs` to today-only (IST timestamp comparison, not string prefix). Uses `(s) => s.scanLogs` selector (no price-tick re-renders).
- `AgentFeed.tsx` — Agent action log. All/Real/Shadow filter. Purple `SHADOW` pill for shadow entries. Trailing SL detection: `action_type === "SL_TRIGGERED"` + `details.reason === "TRAILING_SL"` → amber "TRAILING SL" text. Uses `(s) => s.agentLogs` selector (no price-tick re-renders).

**shared/**
- `SymbolSearchInput.tsx` — Reusable debounced symbol search with autocomplete. Props: `onSelect(result)`, `placeholder?`, `segmentFilter?`. 300ms debounce. Shows "Only equity stocks can be added here" when `segmentFilter` filters out all results. Exports `SymbolResult` interface. Used by `dashboard/Watchlist.tsx` and `intraday-futures/PermanentWatchlist.tsx`.

**signals/**
- `SignalHistoryPanel.tsx` — Signal version history. Takes `signalId: string`, manages its own fetch (lazy, once). Each version row: version badge, time (IST), Entry/SL/Tgt, confidence, AI adjustment, AI action badge, blocked italic. Used by `signals/page.tsx` and `ScannerPanel.tsx`.

**research/**
- `ResearchSearch.tsx` — Symbol autocomplete (EQ + INDEX), debounced 300ms, "Analyze" button.
- `ResearchProgress.tsx` — Live agent status tracker: 6 agents as dots (pending/running/done/failed) with labels and durations. Updates via WebSocket `research:*` events.
- `ResearchReport.tsx` — Full report display: recommendation badge, confidence meter, executive summary, ActionableLevels card, risks/catalysts, expandable section cards. LLM text via `<Markdown>`. Accepts optional `onClose` prop; Escape key wired in `research/page.tsx`.
- `Markdown.tsx` — Thin `react-markdown` + `remark-gfm` wrapper with dark theme overrides and amber accents. Used for all LLM-generated text in `ResearchReport`.
- `ReportHistory.tsx` — Past reports list: symbol, recommendation badge, confidence, relative time.
- `ActionableLevels.tsx` — Visual card: entry zone, SL, T1/T2, R:R ratio, timeframe, long-term suitability.
- `RecommendationBadge.tsx` — BUY/HOLD/SELL badge (color-coded) + confidence percentage bar.

**trades/**
- `PeriodFilter.tsx` — Period pill bar (Today / Week / 30D / 3M / Custom). 30D = rolling last 30 days; 3M = rolling last 3 months via `subDaysIST`/`subMonthsIST`. Custom opens two date inputs + Apply. Emits `{ start, end, label }`.
- `SummaryStrip.tsx` — Inline metrics strip: total P&L, closed trades, open count badge, win rate, best/worst day, avg P&L, profit factor. Shows "net" tag next to P&L label when `showNetPnL` is on. Accepts `showNetPnL?` (uses `net_pnl`, falls back to gross), `peakMargin?` (amber accent when > 0), and `label?` (shown as prefix, amber border styling when set — used for "HOLD" strip).
- `PnLHeatmap.tsx` — Continuous week-strip heatmap (GitHub-style). Mon–Fri only, single strip across period. Cell color: `rgba` green/red by alpha (0.30–0.95). Clicking a cell fires `onSelectDay`; clicking again deselects. Today has amber ring.
- `TradesTable.tsx` — Dense table with expandable `TradeDetailsPanel` rows. Exports `HoldData` type (`exitPrice`, `pnl`, `pnlPercent`, `netPnl`, `chargesJson`, `exitTime`, `outcome`). Accepts `showSource?`, `showSignalData?`, `simLots?`, `showNetPnL?`, `holdData?`, `holdScenario?`. Columns: Date/Time (entry + exit time + signal-to-fill latency), Symbol, Type, Lots (qty), Entry, Exit, SL/Tgt, P&L (net toggle + charges tooltip), [Hold Exit], [Hold P&L], [Hold Exit Time / Exit Time], [Outcome], Strategy, [Conf], [AI], Exit Reason, Margin, Status, [Source]. Hold columns (3) appear when `holdData` is provided with entries; Outcome column (+1, total 4) only appears when `holdScenario === "sl_tgt"`. Hold P&L column shows net P&L when `showNetPnL` is on, with "i" charges tooltip — same pattern as actual P&L. Signal snapshot in details row: AI badge, AI summary/rationale, confidence factors bar (strategy-aware), supports/risks grid, `SignalHistoryPanel`.

**options/**
- `AgentLog.tsx` — Reverse-chronological Strategy 2 gate diagnostics. Paginated (100/page), IntersectionObserver infinite scroll, 10s auto-refresh in live mode. Category filter pills: GATE (red), SIGNAL (green), SKIP (grey).

**intraday-futures/**
- `DayStatusBar.tsx` — Phase badge, agent status, date picker, action buttons (Briefing, Screener, Pause/Resume — hidden in historical mode). Polls every 5s (live mode only). Historical mode reads phase from Redis.
- `Watchlist.tsx` — Sortable/filterable Strategy 5 screener table. Filter select: All/Screened/Pinned. Columns: symbol, score, RS percentile, ADR%, Gap%, bias (dot indicator for gap-override with CSS tooltip), LLM confidence badge, news sentiment, ORB range, live LTP. Symbol names clickable when `onOpenChart` prop provided. Polls every 30s in live mode. Conf badge expands `llm_reason` row. `InfoTip` component on Score + Conf column headers.
- `PermanentWatchlist.tsx` — Compact card for managing always-pinned watchlist. Loads from `GET /api/v1/intraday-futures/permanent-watchlist`. `SymbolSearchInput` (EQ-filtered) for adding. Optimistic add/remove with 422 rollback.
- `AgentLog.tsx` — Reverse-chronological Strategy 5 activity. Paginated (100/page), IntersectionObserver scroll, 10s poll for first page. Category badges with color coding. Category filter toggle pills. React keys use `timestamp-category-index`.
- `SetupPerformance.tsx` — Collapsible per-setup win rate bars, W/L counts, P&L. Fetches via `getIntradayFuturesSetupPerformance()`.
- `GlobalCues.tsx` — Collapsible morning briefing + global market cues. Manual `↻ refresh` button passes `force=true` to bypass Redis cache. Shows: overnight bias badge, global score bar (BiasBar), Nifty Gap, Nifty/S&P/Nasdaq/Dow Futures/Crude/USD-INR/DXY/India VIX/US VIX. `preopen_reassessed` shown as `pre-open ✓` badge.

**positions/**
- `ActivePositions.tsx` — Dense table of open positions: Symbol, Strike, Entry, LTP, P&L, SL Dist, Strategy, Close button. `P` badge on permanent watchlist positions (open) and closed-today trades. Expanded detail: Opened time + fill latency, Strategy, Expiry, Lots/Qty, Stop Loss, Target, Margin (`pos.margin_required` coerced via `Number()`). Closed Today section shows fill latency from `signal_snapshot.generated_at`. Watchlist button resolves symbol via `api.searchSymbols()`. Confidence filter from store. Source pills `[Manual | <profile1> | … | Shadow]` driven by `yoloProfiles` store — for profile mode, filters positions by `yolo_profile_id`; for manual mode, filters by `source === "MANUAL"`. Direction-aware P/L and SL distance. Trailing SL exit reason in amber. `PositionRows` is a module-level component (not an inner function) to prevent React remount flickering on price ticks.

---

### `src/lib/` - Utilities

#### lib/api.ts
All API calls go through this module via a single `request()` helper (parses error responses — string/array/other `detail` shapes). Environment-aware base URL: port 3000 → `:8080`, otherwise same host.

**Market**
- `api.health()` — `GET /api/v1/health`. Returns `data_feed_ready` (bool) and `startup_error` (str|null) in addition to version/deployed_at. Used by: Header
- `api.getPrice(symbol)` — `GET /api/v1/market/price/{symbol}`
- `api.getAllPrices()` — `GET /api/v1/market/prices`. Used by: Header
- `api.refreshQuotes()` — `POST /api/v1/market/feed/refresh`
- `api.getOHLCV(symbol, {resolution?, days?})` — `GET /api/v1/market/ohlcv/{symbol}`. Used by: PriceChart
- `api.getMarketStatus()` — `GET /api/v1/market/status`. Used by: Header
- `api.searchSymbols(query)` — `GET /api/v1/market/symbols/search?q=`. Used by: SymbolSearchInput, ResearchSearch, ActivePositions
- `api.fetchBatchPrices(symbols[])` — `POST /api/v1/market/prices/batch`. Used by: intraday-futures/Watchlist
- `api.startDataFeed()` / `api.stopDataFeed()` — `POST /api/v1/market/feed/start|stop`
- `api.getFyersStatus()` — `GET /api/v1/auth/fyers/status`. Used by: Header

**Positions**
- `api.getPositions(opts?)` — `GET /api/v1/positions`. `opts`: `{ includeShadow?, yolo_profile_id? }`. Used by: ActivePositions
- `api.closePosition(id, reason?)` — `POST /api/v1/positions/{id}/close`. Used by: ActivePositions
- `api.updateSL(id, stopLoss)` — `PATCH /api/v1/positions/{id}/sl`

**Trades**
- `api.getTrades(params?)` — `GET /api/v1/trades` with optional filters: `status`, `source`, `yolo_profile_id`, `strategy`, `limit`, `entry_since/until`, `min/max_confidence`, `ai_action`, `instrument_type`, `signal_type`, `min/max_lots`, `exclude_permanent`. Used by: trades/page
- `api.getClosedTradesToday(source?)` — `GET /api/v1/trades?status=CLOSED&closed_since={IST-midnight}`. Used by: ActivePositions, dashboard/page
- `api.getTradeSummary(opts?)` — `GET /api/v1/trades/summary`. `opts`: `{ source?, yolo_profile_id?, exclude_permanent? }`. Used by: trades/page
- `api.holdAnalysis(tradeIds, scenario)` — `POST /api/v1/trades/hold-analysis`, scenario: `"best"|"worst"|"eod"|"sl_tgt"` → `HoldAnalysisResponse`. Used by: trades/page
- `api.marginAnalysis(tradeIds[])` — `POST /api/v1/trades/margin-analysis` → `{peak_margin, peak_time, total_margin, trade_count}`. Used by: trades/page

**Signals**
- `api.getSignals(params?)` — `GET /api/v1/signals` with `status`, `generated_since/until`, `strategy`, `limit`. Used by: signals/page, dashboard/page
- `api.getActiveSignals()` — `GET /api/v1/signals/active`
- `api.previewSignal(id)` — `GET /api/v1/signals/{id}/preview` → `SignalPreview`. Used by: ExecuteSignalModal
- `api.executeSignal(id, opts?)` — `POST /api/v1/signals/{id}/execute`. Used by: ExecuteSignalModal
- `api.rejectSignal(id)` — `POST /api/v1/signals/{id}/reject`. Used by: ScannerPanel
- `api.getSignalHistory(id)` — `GET /api/v1/signals/{id}/history` → `SignalHistory[]`. Used by: SignalHistoryPanel

**Risk / Agent**
- `api.getRiskDashboard(yolo_profile_id?)` — `GET /api/v1/risk/dashboard`. Optional `yolo_profile_id` scopes all top-level metrics (daily_pnl, closed_pnl, trades_today, notional, risk, margin_utilized, is_profit_capped) to that profile; `profiles[]` array always covers all active profiles. Used by: dashboard/page, agent/page, AgentPopup
- `api.getAgentStatus()` — `GET /api/v1/agent/status`. Used by: agent/page, AgentPopup
- `api.startAgent()` / `api.stopAgent()` — `POST /api/v1/agent/start|stop`. Used by: AgentPopup
- `api.getAgentLogs(opts?)` — `GET /api/v1/agent/logs` with `limit`, `since`, `until`. Used by: agent/page
- `api.confirmAction(logId, approved)` — `POST /api/v1/agent/confirm/{logId}`. Used by: agent/page
- `api.toggleYolo(enabled)` — `PATCH /api/v1/agent/yolo`. Used by: AgentPopup

**YOLO Profiles**
- `api.getYoloProfiles()` — `GET /api/v1/yolo-profiles` → `YoloProfile[]`. Used by: trades/page, ActivePositions, settings/page, page.tsx (dashboard)
- `api.createYoloProfile(data)` — `POST /api/v1/yolo-profiles`. Used by: settings/page
- `api.updateYoloProfile(id, data)` — `PATCH /api/v1/yolo-profiles/{id}`. Used by: settings/page
- `api.deleteYoloProfile(id)` — `DELETE /api/v1/yolo-profiles/{id}`. Used by: settings/page

**Watchlist**
- `api.getWatchlist()` — `GET /api/v1/watchlist`. Used by: dashboard/Watchlist, ChartModal
- `api.addToWatchlist(item)` — `POST /api/v1/watchlist`. Used by: dashboard/Watchlist
- `api.removeFromWatchlist(symbol)` — `DELETE /api/v1/watchlist/{symbol}`. Used by: dashboard/Watchlist

**Strategies**
- `api.getStrategies()` — `GET /api/v1/strategies`. Used by: settings/page
- `api.toggleStrategy(name)` — `PATCH /api/v1/strategies/{name}/toggle`. Used by: settings/page
- `api.toggleAutoMode(name)` — `PATCH /api/v1/strategies/{name}/auto-mode`. Used by: settings/page
- `api.updateStrategy(name, body)` — `PUT /api/v1/strategies/{name}`. Used by: settings/page
- `api.getParameterDefaults(name)` — `GET /api/v1/strategies/{name}/parameter-defaults`. Used by: settings/StrategyParams
- `api.evaluateStrategy(strategyName, symbol)` — `POST /api/v1/strategies/evaluate`. Used by: ScannerHeader
- `api.evaluateStrategyBatch(strategyName)` — `POST /api/v1/strategies/evaluate/batch`. Used by: ScannerHeader

**Settings**
- `api.getTradingSettings()` — `GET /api/v1/settings/trading`. Used by: settings/page
- `api.updateTradingSettings(patch)` — `PATCH /api/v1/settings/trading`. Used by: settings/page

**Research**
- `api.startResearch(symbol)` — `POST /api/v1/research/start`. Used by: research/page
- `api.getResearchReports(params?)` — `GET /api/v1/research/reports`. Used by: research/page
- `api.getResearchReport(id)` — `GET /api/v1/research/reports/{id}`. Used by: research/page
- `api.deleteResearchReport(id)` — `DELETE /api/v1/research/reports/{id}`. Used by: research/page

**Strategy 5 — Intraday Futures**
- `api.getIntradayFuturesWatchlist(date?)` — returns `S5WatchlistItem[]`. Used by: intraday-futures/Watchlist
- `api.getIntradayFuturesAgentLog(date?, offset?, limit?)` — returns `{entries, total}`. Used by: intraday-futures/AgentLog
- `api.getIntradayFuturesGlobalCues(date?, force?)` — `force=true` bypasses Redis cache. Used by: GlobalCues
- `api.getIntradayFuturesBriefing(date?)` — returns `S5MorningBriefing`. Used by: GlobalCues
- `api.getIntradayFuturesDailyStats(date?)` — returns `S5DailyStats`. Used by: intraday-futures/page
- `api.getIntradayFuturesPhase(date?)` — returns `{phase}`. Used by: DayStatusBar
- `api.getIntradayFuturesAgentStatus()` — returns `{status}`. Used by: DayStatusBar
- `api.getIntradayFuturesSetupPerformance(date?, days?)` — returns `S5SetupPerformance`. Used by: SetupPerformance
- `api.runIntradayFuturesScreener()` — `POST /api/v1/intraday-futures/screener/run`. Used by: DayStatusBar
- `api.runIntradayFuturesBriefing()` — `POST /api/v1/intraday-futures/briefing/run`. Used by: DayStatusBar
- `api.setIntradayFuturesAgentAction(action)` — `POST /api/v1/intraday-futures/agent/{pause|resume}`. Used by: DayStatusBar
- `api.getPermanentWatchlist()` — returns `{symbols}`. Used by: PermanentWatchlist
- `api.addToPermanentWatchlist(symbol)` — `POST`, returns `{symbols}`. Used by: PermanentWatchlist
- `api.removeFromPermanentWatchlist(symbol)` — `DELETE`, returns `{symbols}`. Used by: PermanentWatchlist

**Options — Strategy 2**
- `api.getOptionsWindowState()` — returns `{window_state, market_open}`. Used by: options/page
- `api.getOptionsAgentLog(date?, offset?, limit?)` — returns `{entries, total}`. Used by: options/AgentLog

**Tasks**
- `api.getTasks()` — `GET /api/v1/tasks` → `{tasks: BackgroundTask[]}`. Used by: TasksPopup

---

#### lib/formatters.ts
- `formatINR(value)` — INR with Indian number system (en-IN locale, 2 decimal places). Used by: most price displays
- `formatINRCompact(value)` — Compact: `₹1.5L`, `₹2.3Cr`, `₹1.5K` thresholds. Used by: PnLCard, SummaryStrip
- `formatPercent(value)` — `+2.50%` with sign. Used by: price change displays
- `formatTime(timestamp)` — UTC string → IST HH:MM:SS. Used by: signal cards, trade rows
- `formatDate(timestamp)` — UTC string → IST `24 May 2026`. Used by: trade rows
- `pnlColor(value)` — `"text-profit"` | `"text-loss"` | `"text-text-secondary"`. Used by: all P&L displays
- `toISTDate(d)` — `Date` → IST-adjusted `Date` object
- `startOfDayIST(d)` / `endOfDayIST(d)` — IST day boundaries as UTC `Date`
- `startOfMonthIST(d)` / `endOfMonthIST(d)` — IST month boundaries
- `startOfWeekIST(d)` — IST Monday 00:00 of the week containing `d`
- `subDaysIST(d, n)` / `subMonthsIST(d, n)` — subtract n days/months in IST
- `eachDayInRange(start, end)` — array of `startOfDayIST` dates between start and end
- `isoDateIST(d)` — `YYYY-MM-DD` string in IST. Used by: date pickers, API params
- `formatDateShort(d)` — `"24 May"` in IST locale. Used by: PnLHeatmap
- `monthLabel(d)` — `"May 2026"` in IST locale. Used by: PnLHeatmap

---

#### lib/constants.ts
- `SYMBOLS` — `["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"]` as const. Used by: SymbolSelector, PriceChart, ChartModal
- `STRATEGY_LABELS` — `Record<string, string>`: `orb` → "ORB", `vwap_pullback` → "VWAP Pullback", `gamma_scalping` → "Gamma Scalp", `can_slim` → "CAN SLIM", `intraday_futures` → "Intraday Futures". Used by: ScannerHeader, signal cards, trade rows
- `STATUS_COLORS` — `Record<string, string>` Tailwind classes per signal/trade status (OPEN/CLOSED/PENDING/EXECUTED/REJECTED/EXPIRED). Used by: signal and trade status badges
- `getWsUrl()` — environment-aware WS URL: port 3000 → `ws://{host}:8080/ws`, otherwise `ws://{host}/ws`. Used by: useWebSocket
- `Timeframe` — type `"1m" | "5m" | "15m" | "1h" | "1D"`. Used by: PriceChart, store
- `displaySymbol(symbol)` — strips exchange prefix (`"NSE:FOO"` → `"FOO"`). Used by: signal cards

---

#### lib/types.ts — Key Interfaces
- `PriceData` — `{symbol, ltp, bid, ask, volume, change, change_pct, timestamp}`
- `Candle` — `{timestamp, open, high, low, close, volume}`
- `YoloProfile` — `{id, name, profit_cap, is_active, sort_order, is_capped_today?}`
- `Position` — open position; key fields: `is_shadow`, `signal_confidence`, `signal_generated_at`, `margin_required`, `fyers_option_symbol`, `position_type`, `is_permanent_watchlist`, `yolo_profile_id: string | null`
- `Trade` — closed/open trade; key fields: `source` (`MANUAL|YOLO|SHADOW`), `charges_json` (brokerage/STT/exchange/GST/SEBI/stamp/total), `net_pnl`, `margin_required`, `is_permanent_watchlist`, `yolo_profile_id: string | null`, `signal_confidence/ai_action/ai_summary/instrument_type/signal_type` (snapshotted columns), `signal_snapshot` (full JSONB), `signal_is_permanent_watchlist`
- `Signal` — trading opportunity; key fields: `signal_type` (`BUY_CE|BUY_PE|BUY_FUT|SELL_FUT`), `instrument_type` (`OPTION|FUTURE|EQUITY`), `confidence`, `is_permanent_watchlist`, `ai_summary/rationale/adjustment/action`; **no `lots` or `quantity` fields** (resolved at execution via preview endpoint)
- `SignalHistory` — Case-2 snapshot; `version`, `entry_price/stop_loss/target_price`, `confidence`, `ai_*` fields, `captured_at`
- `SignalPreview` — from `GET /signals/{id}/preview`: `{lots, quantity, lot_size, entry_price, stop_loss, target_price, risk, notional, margin_required, sizing_meta, warnings[]}`
- `RiskDashboard` — `{capital, daily_pnl, closed_pnl, daily_drawdown_pct, max_daily_drawdown_pct, trades_today, max_trades_per_day, notional, risk, margin_utilized, is_halted, is_profit_capped, positions_open, profiles: Array<{id, name, profit_cap, current_pnl, is_capped}>}`
- `MarketStatus` — `{is_open, in_trading_window, in_dead_zone, minutes_to_close, india_vix, cpr_type, day_bias, fyers_connected}`
- `AgentStatus` — `{running, yolo_mode, autonomy_level: "manual"|"semi"|"yolo", last_action_at, pending_confirmations, positions_monitored, uptime_seconds}`
- `AgentLog` — agent action record with `action_type`, `details`, `requires_confirmation`, `confirmation_status`
- `BackgroundTask` — `{name, type: "scheduler"|"startup"|"service", status, started_at, completed_at, error, metadata}`
- `ResearchReport` — `{status: "PENDING"|"IN_PROGRESS"|"COMPLETED"|"PARTIAL"|"FAILED", recommendation: "BUY"|"HOLD"|"SELL"|"AVOID"|null, agent_runs: ResearchAgentRun[], ...}`
- `S5WatchlistItem` — screener item; includes `composite_score`, `bias`, `bias_source`, `original_bias`, `gap_pct`, `orb_high/low/range`, `manual` (permanently-pinned flag), `news.headlines[]`
- `S5AgentLogEntry` — `{timestamp: number, category: string, message: string, data?}`
- `S5GlobalCues` — global market snapshot; includes `overnight_bias`, `global_score`, `india_vix_live`, `nifty_gap_pct`, absolute prices (`crude_price`, `sp500_price`, etc.)
- `S5MorningBriefing` — `{approach?, summary?, sector_bias?, setup_priority?, flags?, max_lots_recommendation?}`
- `S5SetupPerformance` — `{period, setups: Record<string, S5SetupStats>, overall}`
- `PerTradeHoldResult` — `{trade_id, hold_exit_price, hold_pnl, hold_net_pnl, hold_charges_json, hold_exit_time, hold_outcome, data_found}`
- `HoldAnalysisResponse` — `{results: PerTradeHoldResult[]}`
- `HoldResultMap` — `Map<string, { hold_exit_price, hold_pnl, hold_net_pnl, hold_charges_json, hold_exit_time, hold_outcome }>` — populated from API response in `trades/page.tsx`

---

### `src/hooks/` - Custom Hooks

#### hooks/useWebSocket.ts
Single hook managing the WebSocket connection. Returns `wsRef`. Auto-reconnects on close (3s delay). Guards all event handlers with `ws === wsRef.current` staleness check to prevent React Strict Mode race conditions. Sends application-level heartbeat ping every 25s to keep the connection alive (backend `manager.py` responds with pong). **All store interactions use `useStore.getState()`** — no reactive subscriptions, so AppShell (which hosts this hook) never re-renders from store changes.

**No symbol subscription filtering** — backend broadcasts all price ticks; frontend receives all prices automatically.

**Events handled:**
- `price:update` → `updatePrice(symbol, data)` (500ms-batched in store)
- `trade:open` → `addPosition(data)` (skips if `data.is_shadow`)
- `position:update` → `updatePosition(id, {current_price, unrealized_pnl})`
- `position:closed` → `removePosition(id)`
- `signal:new` → `addSignal(data)`
- `signal:updated` → `addSignal(data)` (deduplicates by id)
- `risk:update` → `setRisk(data)`
- `agent:action` / `agent:confirmation_request` → `addAgentLog(data)`
- `agent:status` → `setAgentStatus(data)`
- `research:started` → `startResearchSession(reportId, symbol, agentsTotal)`
- `research:agent_started` → `updateResearchAgent(reportId, agentName, {status: "running"})`
- `research:agent_completed` → `updateResearchAgent(reportId, agentName, {status: "completed", summary, duration})`
- `research:agent_failed` → `updateResearchAgent(reportId, agentName, {status: "failed", error})`
- `research:completed` → `completeResearch(reportId, "completed")`
- `research:failed` → `completeResearch(reportId, "failed")`
- `market:bias_update` → `setIntradayBias(data)` (only for `symbol === "NIFTY"`)

---

### `src/store/` - Zustand State

#### store/index.ts
Single store created with `create()` + `persist()` middleware. Storage key: `"scan-logs-storage"` in `localStorage`.

**Slices:**
- `prices: Record<string, PriceData>` — `updatePrice()` uses 500ms batching + LTP dedup (batch flushes once per 500ms `setTimeout`; unchanged LTPs skipped)
- `positions / closedToday / shadowPositions / shadowClosedToday` — position state; `addPosition()` skips shadow events; `prependClosedTrade()` deduplicates by id
- `yoloProfiles: YoloProfile[]` + `setYoloProfiles` — list of YOLO profiles from the API; consumed by ActivePositions, trades/page, PnLCard, dashboard/page
- `dashboardViewMode: string` — `"MANUAL"` | profile UUID | `"SHADOW"`. Drives ActivePositions + PnLCard source filtering. Default: `"MANUAL"`
- `tradesViewMode: string` — `"MANUAL"` | profile UUID | `"SHADOW"`. Drives Trades page source filtering (independent from dashboard). Default: `"MANUAL"`
- `signals` — `addSignal()` deduplicates by id (used for both new signals and dedup updates)
- `scanLogs: ScanLogEntry[]` — capped at 20 entries; `addScanLog()` prepends
- `risk: RiskDashboard | null`
- `marketStatus: MarketStatus | null`
- `agentStatus / agentLogs` — `addAgentLog()` prepends, capped at 200 entries
- `wsConnected: boolean`
- `activeResearches / selectedResearchId / researchReports` — research session state
- `watchlistItems / intradayBias`

**Persisted keys** (via `partialize`):
- `scanLogs`, `activeTimeframe`, `dashboardViewMode` (string — `"MANUAL"` | UUID | `"SHADOW"`), `tradesViewMode` (same values), `showNetPnL`
- `tradesShowOpen`, `tradesExcludePinned`, `tradesPeriodLabel`, `tradesPeriodStart`, `tradesPeriodEnd`, `tradesStrategy`, `tradesSimOpen`, `tradesSim`, `tradesHoldOpen`, `tradesHold: { scenario: "best"|"worst"|"eod"|"sl_tgt" }`
- `scannerShowExecuted`, `scannerMinConfidence`
- `signalsMinConfidence`, `signalsPeriodLabel`, `signalsPeriodStart`, `signalsPeriodEnd`, `signalsStrategy`, `signalsHideInformational`
- `positionsMinConfidence`

**Key store actions:**
- `setTradesPeriod(label, start, end)` — updates period label + ISO strings
- `setTradesSim(partialUpdates)` / `resetTradesSim()` — sim filter state
- `setTradesHoldOpen(open)` / `setTradesHold(updates)` / `resetTradesHold()` — hold analysis panel state
- `setTradesExcludePinned(v)` — "− Pinned" toggle on trades page (sends `exclude_permanent=true` to API)
- `setScannerShowExecuted(v)` — when true, dashboard fetches EXECUTED signals alongside PENDING
- `setPositionsMinConfidence(v)` — shared by ActivePositions slider and PnLCard

---

### `src/__tests__/` - Unit Tests (Vitest)

Pure-logic unit tests for module-level functions that are not exported. Pattern: copy the function verbatim into the test file with a `// Copied from …` comment so the test is self-contained and refactoring doesn't silently break it.

_(No unit tests currently — `applyHoldAnalysis.test.ts` was removed when `applyHoldAnalysis()` was deleted; hold P&L is now computed on the backend.)_

---

## Theme — Institutional Terminal (Dark Only)

Bloomberg-inspired hedge fund terminal aesthetic. Dense, monospace-forward, warm amber accent.

```
Background:  #06060b  (--bg-primary)
Surface:     #0b0b13  (--bg-secondary)
Tertiary:    #12121c  (--bg-tertiary)
Elevated:    #181825  (--bg-elevated)
Border:      #1a1a2a
Profit:      #00e68a  (green)
Loss:        #ff4060  (red)
Accent:      #d4a843  (warm amber/gold — institutional)
Warning:     #f59e0b  (amber, YOLO badge)
Font:        Geist Sans + Geist Mono
```

### Design Principles
- **Density first** — tight padding (py-1.5, px-3), compact rows, minimal whitespace
- **Monospace-forward** — all data, labels, prices, timestamps in `font-mono`
- **Sharp corners** — `rounded` (4px) instead of `rounded-lg` (8px)
- **Font sizes** — 10px labels/badges, 12px (text-xs) data and primary text, text-sm for key values
- **Uppercase tracking** — section headers use `uppercase tracking-wider font-mono`
- **Dividers over cards** — inline dividers (`w-px h-4 bg-border`) separate metrics
- **Subtle depth** — noise texture overlay (`noise-bg`), glow utilities (`glow-profit`, `glow-loss`, `glow-accent`)
- **Amber accent** — warm gold (#d4a843) for active states, badges, selections

### CSS Utilities (globals.css)
- `.noise-bg` — subtle fractal noise texture overlay (opacity 0.015)
- `.glow-profit` / `.glow-loss` / `.glow-accent` — colored box-shadow glows
- `.animate-fade-in` — 200ms fade-in with translateY for expanded panels

### Layout Dimensions
- Sidebar: 48px wide (`w-12`)
- Header: 36px tall (`h-9`)
- Main content: `fixed top-9 left-12 right-0 bottom-0 p-3 overflow-y-auto` (main is the scroll container)

## Zustand Performance Rules
**Never use `useStore()` without a selector** — it re-renders the component on every single store mutation. Always scope subscriptions:

```tsx
// WRONG — re-renders on every WS event
const { prices, positions } = useStore();

// CORRECT — prices re-render scoped to price ticks only
const prices = useStore((s) => s.prices);

// CORRECT — non-price fields: useShallow stops re-renders from unrelated slices
import { useShallow } from "zustand/react/shallow";
const { positions, closedToday } = useStore(useShallow((s) => ({
  positions: s.positions,
  closedToday: s.closedToday,
})));

// CORRECT — setter-only usage: use getState() to avoid subscribing entirely
// Use this in callbacks, effects, and event handlers that only call store actions.
const handleClick = useCallback(() => {
  useStore.getState().setSelectedSymbol(symbol);
}, [symbol]);
```

**`useWebSocket` uses `getState()` exclusively** — the hook only calls store actions (setters), never reads state reactively. This ensures AppShell (which hosts the hook) never re-renders from store changes, preventing full-tree cascade re-renders.

**`updatePrice` uses 500ms batching + LTP dedup** — price ticks accumulated in `_pendingPrices`, flushed via `setTimeout(500ms)` (~2 updates/sec). Unchanged LTPs skipped. Do not remove the batching or the LTP check.

**`prices` is a shared map for ALL subscribed symbols** — backend broadcasts all prices. Components read only the keys they need.

## Conventions
- All pages are client components (`'use client'`)
- All API calls go through `lib/api.ts` — never raw fetch in components
- WebSocket events go through `hooks/useWebSocket.ts`
- Currency formatted as INR with Indian number system (e.g., Rs 1,50,000)
- All 5 indices always referenced: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
- Backend API base URL: dev (port 3000) → `http://{host}:8080`; prod (port 80 via nginx) → `http://{host}`
- WebSocket URL: dev → `ws://{host}:8080/ws`; prod → `ws://{host}/ws` (nginx proxies to backend)
- `next.config.ts` sets `output: "standalone"` (Docker) and `allowedDevOrigins: ["192.168.*.*", "100.*.*.*"]`
- Strategy badges: `text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent`
- Section headers: `text-xs font-mono font-medium text-text-secondary uppercase tracking-wider`
- Empty states: `text-xs font-mono text-text-muted` with lowercase text

## Doc-Update Rules (Frontend)
When adding or changing frontend code, update this file:
- **New component**: add a 1-2 line entry in the appropriate domain section (`components/{domain}/`)
- **New API function**: add a bullet under the appropriate `lib/api.ts` group with endpoint + return type + `Used by:`
- **New type/interface**: add to `lib/types.ts` key interfaces section
- **New store slice or persisted key**: update `store/index.ts` Slices and Persisted keys sections
- **New WebSocket event**: add to hooks/useWebSocket.ts Events handled list
- **Changed component props or behavior**: update the component's 1-2 line description
- **New page**: add to the `src/app/` pages list (update the count in the heading)
- Prefer editing existing entries over adding new ones — restructure if needed

## How-To Guides

### Add a New Page
1. Create `src/app/{page-name}/page.tsx` with `'use client'`
2. Add nav link in `src/components/layout/Sidebar.tsx`
3. Create domain components in `src/components/{domain}/`
4. Add store slice in `src/store/index.ts` if page needs its own state
5. Update page count and entry in this file's `src/app/` section

### Add a New Component
1. Place in `src/components/{domain}/` matching the page domain
2. Use Tailwind with theme variables (--bg-primary, --profit, --loss, --accent, etc.)
3. Follow terminal aesthetic: monospace text, tight padding, sharp corners, uppercase headers
4. Get data from Zustand store or pass as props — don't fetch inside components
5. Use `formatINR()`, `formatPercent()` from `lib/formatters.ts`
6. Add entry to this file's domain section

### Add a New API Call
1. Add function in `src/lib/api.ts`
2. Add TypeScript types in `src/lib/types.ts`
3. Call from component or store action — not directly from hooks
4. Add entry to this file's `lib/api.ts` registry

### Add a New WebSocket Event
1. Add event type in `src/hooks/useWebSocket.ts` handler
2. Add store action in `src/store/index.ts` to process the event
3. Backend must publish to Redis channel for the event to flow through
4. Add event to this file's hooks/useWebSocket.ts Events list

### Add a New Unit Test
1. Create `src/__tests__/{subject}.test.ts`
2. If the function under test is not exported, copy it verbatim into the test file with a `// Copied from <path>` comment
3. Use a `makeTrade()` / `makeX()` helper for default fixture objects — avoids repeating required fields
4. Run `npm test` to confirm all tests pass
5. Add an entry to `src/__tests__/` section in this file
