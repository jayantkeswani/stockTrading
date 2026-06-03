# Frontend - Next.js / React / TypeScript

## Tech Stack
- Next.js 15 (App Router)
- TypeScript (strict mode)
- Tailwind CSS v4 — institutional terminal aesthetic. Desktop is dark-only; the **mobile shell adds a light theme** via the `data-theme` attribute mechanism (see the Theme section)
- Zustand for state management
- TradingView lightweight-charts for price charts
- WebSocket for real-time updates from backend on port 8080
- **Vitest** — unit test runner (`npm test` / `npm run test:watch`). Config: `vitest.config.ts` (node environment, `@/*` alias wired). Tests live in `src/__tests__/`.

## Module Map

### `src/app/` - Pages (App Router, 10 pages)
All pages use `'use client'` directive.

- `layout.tsx` — Root layout with AppShell wrapper. Exports `viewport` (`width=device-width, initialScale=1, maximumScale=1`) so the responsive shell triggers on phones. Sets `data-theme="dark"` on `<html>` — the whole-app default (so desktop + pre-mount render dark with no flash); the mobile light theme overrides it on the MobileShell subtree only.
- `page.tsx` — Dashboard: sticky PnLStrip at top, 8-col left (ScannerHeader, ScannerPanel, ActivePositions, FuturesWatchlist) + 4-col right (Watchlist, ScanFeed, AgentFeed). ChartModal overlay. Fetches YOLO profiles on mount alongside other dashboard data.
- `trades/page.tsx` — Kite-style P&L dashboard. Filter state (period, strategy, Shadow/Real, sim panel) lives in Zustand store, persisted via `persist` middleware. SummaryStrip, PnLHeatmap, TradesTable, Margin Analysis section. Source pills `[Manual | <profile1> | … | Shadow]` driven by `yoloProfiles` store — fetched on mount. Active pill drives data fetch: `source: "MANUAL"`, `yolo_profile_id: <uuid>`, or `source: "SHADOW"`. Net P&L toggle, "+ Open" toggle, "− Pinned" toggle. Simulation filter panel: min confidence, AI action, simulate lots, instrument type, signal type — all applied client-side via `applySimLots()` (scales P&L, charges, and `margin_required` by lot ratio). **Hold Analysis panel**: "Hold ▲/▾" toggle (amber when open). Panel has Scenario pills (Best / Worst / EOD / SL/TGT). On open/scenario change, fetches `POST /trades/hold-analysis` for all closed trade IDs; backend returns per-trade `hold_exit_price`, `hold_pnl`, `hold_net_pnl`, `hold_charges_json`, `hold_exit_time`, and `hold_outcome` from `market_data_1m` (window: entry_time → hold cutoff, where cutoff = min(next same-instrument same-side re-entry, 15:30 IST)). Best=max high, Worst=min low, EOD=last candle close, SL/TGT=first SL or TGT hit (fallback to EOD close). `hold_outcome` only populated for `sl_tgt` scenario. Computes `holdDataMap` (keyed by trade ID) for extra table columns. Passes `holdScenario` to TradesTable for conditional Outcome column. Pipeline: `trades → filteredTrades → simTrades → {SummaryStrip, dailyPnL→PnLHeatmap, displayedTrades→TradesTable(+holdDataMap, holdScenario)}`. Second `SummaryStrip` with `label="HOLD"` renders below when hold is active and respects `showNetPnL` toggle via `holdDataMap` directly. TradesTable shows "Hold Exit", "Hold P&L", "Hold Exit Time / Exit Time" columns alongside actual values, plus "Outcome" column only when `sl_tgt` scenario is active.
- `signals/page.tsx` — Signal history feed. PeriodFilter + strategy pills + min-confidence slider + symbol/reason search bar. All filters persisted. `ConfidenceFactorsBar` uses strategy-aware label maps. `SignalCard` shows an "↻ Updated" badge when `update_count > 0` (signal was deduped/revised) and includes `SignalHistoryPanel`.
- `settings/page.tsx` — Strategy config (is_active, auto_mode, shadow_enabled, yolo_enabled, symbols with autocomplete + group presets), risk params, StrategyParams collapsible numeric inputs, "Skip Pinned Signals" row with Shadow/YOLO toggles. Save validation includes confidence tier ordering check. **YOLO Profiles section**: CRUD management for profiles (name, profit cap, active toggle, plus an "Inval" thesis-invalidation control — toggle sets `invalidation_persist` 3↔0, a persist number input, and a "Q" quorum toggle). Profile changes call the YOLO profile API directly (no page-level save needed). CRUD operations sync to Zustand store (`setYoloProfiles`) so Dashboard/Trades pills update without page reload. Uses `committedProfilesRef` to track API-known values for onBlur dirty-checking.
- `research/page.tsx` — AI Research: symbol search, real-time agent progress, full report with expandable cards, past reports history.
- `agent/page.tsx` — Agent dashboard: YOLO toggle (disabled + grey when agent stopped), 6-card status grid (Mode card shows "OFF" when stopped, "YOLO"/"SEMI" when running; Profit Cap card shows per-profile status from `risk.profiles[]`), activity log. PeriodFilter, action-type pills, strategy pills, All/Real/Shadow filter, symbol text input, "Pending" checkbox. Toggle handlers re-fetch full status from API after mutation (no optimistic spread).
- `chart/page.tsx` — Full TradingView chart page. Symbol tab row (all 5 indices + watchlist items).
- `options/page.tsx` — Strategy 2 (VWAP Pullback) dedicated page. DayStatusBar-style header + window state badge. AgentLog in col-span-8.
- `intraday-futures/page.tsx` — Strategy 5 dedicated page. Fixed DayStatusBar, fixed-height two-column grid (`h-[calc(100vh-96px)]`) with per-column `overflow-y-auto`. 8-col left (Watchlist + PermanentWatchlist), 4-col right (AgentLog, SetupPerformance, GlobalCues). Config managed via Settings page. ChartModal overlay pattern.

### `src/components/` - React Components (by domain)

**layout/**
- `AppShell.tsx` — Main wrapper. Branches on viewport via `useIsMobile()`: phones (≤768px) render `<MobileShell />` (the desktop route `children` are NOT rendered); larger viewports render sidebar (48px, fixed) + header (36px, fixed) + content area. While `isMobile === null` (pre-mount) renders a neutral `bg-bg-primary` div to avoid a hydration flash. Desktop `<main>` is `fixed top-9 left-12 right-0 bottom-0` with `overflow-y-auto` — the scroll container so `sticky` elements work. Applies `noise-bg` texture. `useWebSocket()` runs in both modes.
- `Header.tsx` — Thin status bar (36px): IST clock, market status, VIX, Tasks/Fyers/Agent/WS indicators, "STARTING…" pulse when `data_feed_ready=false` (polls health every 3s until ready). Polls `getAllPrices()` + market status every 10s. Uses `useShallow` selector (no price-tick re-renders). `BiasIndicator` sub-component uses scoped `(s) => s.intradayBias` selector and dims (opacity-40 + `~`) when the bias is >5min stale. Hydrates `intradayBias` once on mount via `api.getIntradayBias()` (covers a cold load and after-close display; the persisted store value covers refreshes; WS `market:bias_update` keeps it live).
- `TasksPopup.tsx` — Background tasks popup (click-to-open). Shows all registered tasks with status dots, type badges, schedule/description, timestamps, errors. Polls every 5s while open.
- `AgentPopup.tsx` — Agent control popup: start/stop, SEMI/YOLO toggle, positions monitored, pending confirmations, uptime. Profit Cap section shows per-profile cap status with current P&L vs cap for each active profile. Reads Zustand store, writes via API. Click-outside to close.
- `Sidebar.tsx` — Narrow icon rail (48px): Dashboard → Futures (trending-up) → Options (bar chart) → Research → Trades → Signals (zap) → Agent → Settings.

**charts/**
- `PriceChart.tsx` — TradingView candlestick chart (1m/5m/15m/1h/1D). Active timeframe stored in Zustand. Refresh button re-fetches without recreating chart. Live updates via scoped `(s) => s.prices[s.selectedSymbol]` selector (re-renders only on selected symbol's price change, not all ticks). IST display via `localization.timeFormatter` adding `IST_OFFSET` (19800s) for axis labels only — backend timestamps fed as UTC unix seconds, do NOT shift them. `fitContent()` always called after data load; `rightOffset: 5` reserves space. OHLC legend via `subscribeCrosshairMove`. Three-effect architecture: create/destroy chart, load data, live ticks. **Light-theme TODO** (full-app pass): chart colors are hardcoded for dark — it needs a `data-theme`-aware `layout`/`grid`/`crosshair` config before the light theme reaches any chart surface.
- `ChartModal.tsx` — Modal (90vw × 85vh) for detailed chart view. Symbol tabs: 5 default indices + custom watchlist items (fetched on open). Pop-out opens `/chart?symbol=…` in a new tab.

**dashboard/**
- `PnLCard.tsx` — Single-line strip: Day P&L with inline U/R breakdown, drawdown bar, trades count, NOTIONAL/RISK/MARGIN metrics. Filters by `positionsMinConfidence` from store. Profile-aware: filters positions and closed trades by active profile/manual mode based on `dashboardViewMode`. Shadow mode: computes from `shadowPositions` + `shadowClosedToday`. Live unrealized P&L via shared `livePositionPnl` (`lib/positionPnl.ts`). Prices via `(s) => s.prices` selector; non-price fields via `useShallow`.
- `Watchlist.tsx` — Uniform symbol list (indices + custom items). Search via `<SymbolSearchInput direction="down">` placed at the top (below the header). Custom items stored via `/api/v1/watchlist`. Max-height 300px with scroll.
- `ScannerHeader.tsx` — Ultra-compact strategy pill bar. Triggers manual batch evaluation via `POST /api/v1/strategies/evaluate/batch`. Logs start/end to ScanFeed. Uses `(s) => s.addScanLog` selector (no price-tick re-renders).
- `ScannerPanel.tsx` — Structured signal cards with strategy-aware rendering. A **strategy dropdown** (`scannerStrategy`, options derived from today's matching signals + "All strategies"; shared with the mobile Signals tab), "+ Executed" and "+ Expired" toggles (each adds that status to the always-shown PENDING set), symbol search, confidence filter. Uses `useShallow` for signal state (no price-tick re-renders). Three card sections: header (direction, symbol, `P` badge, timestamp, WindowBadge, confidence, strategy badge), prices (entry/SL/target/R:R + strategy context), actions (EXEC, Watch, Details expander, AI panel, dismiss). Details and AI panels mutually exclusive. AI button shows `✦ AI` when `ai_summary` present.
- `ExecuteSignalModal.tsx` — Pre-trade confirm modal. Fetches `GET /signals/{id}/preview` for live entry price + computed lots. Shows lot stepper, quantity, Notional, Margin, `preview.warnings`. Submits `POST /signals/{id}/execute` with optional lots override.
- `QuickStats.tsx` — Compact stats card: Trades Today (vs max), Open Positions count, Notional. Reads `risk` + `positions` via `useShallow` (no price-tick re-renders). Shows HALTED banner when `risk.is_halted`.
- `SymbolSelector.tsx` — Tab row for selecting the active index symbol. One button per SYMBOL (5 indices). Shows LTP + change % from store `prices`. Prices via `(s) => s.prices` selector; selection state via `useShallow`. Active button styled with amber accent.
- `ScanFeed.tsx` — Compact scan log, max-height 160px. Filters `scanLogs` to today-only (IST timestamp comparison, not string prefix). Uses `(s) => s.scanLogs` selector (no price-tick re-renders).
- `AgentFeed.tsx` — Agent action log. All/Real/Shadow filter. Purple `SHADOW` pill for shadow entries. Trailing SL detection: `action_type === "SL_TRIGGERED"` + `details.reason === "TRAILING_SL"` → amber "TRAILING SL" text. Uses `(s) => s.agentLogs` selector (no price-tick re-renders).

**shared/**
- `SymbolSearchInput.tsx` — Reusable debounced symbol search with autocomplete. Props: `onSelect(result)`, `placeholder?`, `segmentFilter?`, `direction?` (`"up"` default = suggestions open above, for a bottom-placed input; `"down"` = open below + bottom border, for an input placed at the top of a panel/list). 300ms debounce. Shows "Only equity stocks can be added here" when `segmentFilter` filters out all results. Exports `SymbolResult` interface. Used by `dashboard/Watchlist.tsx`, `intraday-futures/PermanentWatchlist.tsx`, `mobile/MobileWatchlist.tsx`.

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
- `PeriodFilter.tsx` — Period pill bar (Today / Week / 30D / 3M / Custom). 30D = rolling last 30 days; 3M = rolling last 3 months via `subDaysIST`/`subMonthsIST`. Custom opens a dropdown popup (absolute-positioned below the button) with two date inputs + Apply; selected range shown inline next to the Custom button. Click-outside closes the dropdown. Emits `{ start, end, label }`.
- `SummaryStrip.tsx` — Inline metrics strip: total P&L, closed trades, open count badge, win rate, best/worst day, avg P&L, profit factor. Shows "net" tag next to P&L label when `showNetPnL` is on. Accepts `showNetPnL?` (uses `net_pnl`, falls back to gross), `peakMargin?` (amber accent when > 0), and `label?` (shown as prefix, amber border styling when set — used for "HOLD" strip).
- `PnLHeatmap.tsx` — Continuous week-strip heatmap (GitHub-style). Mon–Fri only, single strip across period. Cell color: `rgba` green/red by alpha (0.30–0.95). Clicking a cell fires `onSelectDay`; clicking again deselects. Today has amber ring.
- `TradesTable.tsx` — Dense table with expandable `TradeDetailsPanel` rows. Exports `HoldData` type (`exitPrice`, `pnl`, `pnlPercent`, `netPnl`, `chargesJson`, `exitTime`, `outcome`). Accepts `showSource?`, `showSignalData?`, `simLots?`, `showNetPnL?`, `holdData?`, `holdScenario?`. Columns: Date/Time (entry + exit time + signal-to-fill latency), Symbol, Type, Lots (qty), Entry, Exit, SL/Tgt, P&L (net toggle + charges tooltip), [Hold Exit], [Hold P&L], [Hold Exit Time / Exit Time], [Outcome], Strategy, [Conf], [AI], Exit Reason, Margin, Status, [Source]. Hold columns (3) appear when `holdData` is provided with entries; Outcome column (+1, total 4) only appears when `holdScenario === "sl_tgt"`. Hold P&L column shows net P&L when `showNetPnL` is on, with "i" charges tooltip — same pattern as actual P&L. Signal snapshot in details row: AI badge, AI summary/rationale, confidence factors bar (strategy-aware), supports/risks grid, `SignalHistoryPanel`.

**options/**
- `AgentLog.tsx` — Reverse-chronological Strategy 2 gate diagnostics. Paginated (100/page), IntersectionObserver infinite scroll, 10s auto-refresh in live mode. Category filter pills: GATE (red), SIGNAL (green), SKIP (grey).

**intraday-futures/**
- `DayStatusBar.tsx` — Phase badge (live/today only — hidden in historical, matching the Options page's window badge), date picker, action buttons (Briefing, Screener, Pause/Resume — hidden in historical mode). No agent-status badge: the S5 agent run-state is surfaced only via the Pause/Resume button. Polls every 5s (live mode only).
- `Watchlist.tsx` — Sortable/filterable Strategy 5 screener table. Filter select: All/Screened/Pinned. Columns: symbol, score, RS percentile, ADR%, Gap%, bias (dot indicator for gap-override with CSS tooltip), LLM confidence badge, news sentiment, ORB range, live LTP. Symbol names clickable when `onOpenChart` prop provided. Polls every 30s in live mode. Conf badge expands `llm_reason` row. `InfoTip` component on Score + Conf column headers.
- `PermanentWatchlist.tsx` — Compact card for managing always-pinned watchlist. Loads from `GET /api/v1/intraday-futures/permanent-watchlist`. `SymbolSearchInput` (EQ-filtered) for adding. Optimistic add/remove with 422 rollback.
- `AgentLog.tsx` — Reverse-chronological Strategy 5 activity. Paginated (100/page), IntersectionObserver scroll, 10s poll for first page. Category badges with color coding. Category filter toggle pills. React keys use `timestamp-category-index`.
- `SetupPerformance.tsx` — Collapsible per-setup win rate bars, W/L counts, P&L. Fetches via `getIntradayFuturesSetupPerformance()`.
- `GlobalCues.tsx` — Collapsible morning briefing + global market cues. Manual `↻ refresh` button passes `force=true` to bypass Redis cache. Shows: overnight bias badge, global score bar (BiasBar), Nifty Gap, Nifty/S&P/Nasdaq/Dow Futures/Crude/USD-INR/DXY/India VIX/US VIX. `preopen_reassessed` shown as `pre-open ✓` badge.

**positions/**
- `ActivePositions.tsx` — Dense table of open positions: Symbol, Strike, Entry, LTP, P&L, SL Dist, Strategy, Close button. `P` badge on permanent watchlist positions (open) and closed-today trades. Expanded detail: Opened time + fill latency, Strategy, Expiry, Lots/Qty, Stop Loss, Target, Margin (`pos.margin_required` coerced via `Number()`). Closed Today section shows fill latency from `signal_snapshot.generated_at`. Watchlist button resolves symbol via `api.searchSymbols()`. Confidence filter from store. Source pills `[Manual | <profile1> | … | Shadow]` driven by `yoloProfiles` store — for profile mode, filters positions by `yolo_profile_id`; for manual mode, filters by `source === "MANUAL"`. Direction-aware P/L and SL distance via shared `livePositionPnl` (`lib/positionPnl.ts`) — direction is read from the backend's `unrealized_pnl` sign (immune to a stale `target_price`), so long PE/CE options never invert. Trailing SL and INVALIDATION exit reasons shown in amber. `PositionRows` is a module-level component (not an inner function) to prevent React remount flickering on price ticks.

**mobile/** — Phone-only dashboard (Option A: viewport branch in `AppShell`, internal-state tabs, one URL). Reuses the same Zustand stores + `lib/api.ts` functions as the desktop pages; only presentation differs. No backend changes. **Font sizing**: these files use fixed-px text sizes (`text-[9px]`…`text-[17px]`) ~1px larger than the desktop terminal scale — tuned for phone readability without enlarging the desktop UI (which keeps `text-xs`/`text-sm`).
- `MobileShell.tsx` — Full-screen flex shell: header (app title + `MobilePnlPill` + ☀/☾ theme toggle + refresh button), the active tab's content, and a fixed bottom tab bar (**Watchlist / Signals / Positions / Trades** — in that order). The active tab is the persisted store `mobileTab` (not local state) so it survives a refresh; the bottom-nav buttons call `setMobileTab`. The active tab is emphasized with a filled `bg-accent/10` rounded pill behind its accent-colored icon+label. Owns the chart overlay: an `openChart(symbol, display?)` callback (passed to `MobileWatchlist`) sets local state that renders `MobileChartModal`. Renders `MobileStatusBar` between the header and the content. On mount **and on every refresh** (a `refreshKey` counter, threaded to all tabs) hydrates the store via `getYoloProfiles` + `getRiskDashboard` + `getPositions({})` → `setPositions` (open positions are otherwise only fed by WS `trade:open`, which already-open rows don't emit) **+ `getClosedTradesToday()` → `setClosedToday`**; WebSocket keeps them fresh thereafter. The `closedToday` fetch is essential: the header day-P&L pill reads it for the realized leg, so without it the strip shows open-only until the Positions tab is visited, then jumps. A second effect hydrates the **shadow** legs (`getPositions({includeShadow})` + `getClosedTradesToday("SHADOW")`) when `dashboardViewMode === "SHADOW"`, for the same reason. `env(safe-area-inset-*)` padding for notch/home-bar; the brand title is `min-w-0 truncate` and the right control group `shrink-0` so a long P&L pill never wraps/overflows. **Theme**: the root div sets `data-theme={mobileTheme === "light" ? "light" : "dark"}` (persisted store flag) AND carries `text-text-primary` — the explicit color is required because `color` inherits as a *computed* value, so without it un-classed text (symbol names) would inherit the dark-default `color` resolved on `<body>` and render grey in light mode. The toggle flips `mobileTheme` via `setMobileTheme`. Used by: AppShell.
- `MobilePnlPill.tsx` — Header day-P&L pill scoped to the selected book (Manual / YOLO profile / Shadow via persisted `dashboardViewMode`). **Tapping opens a bottom-sheet drawer to switch the book; the choice persists across reloads and is shared with the Positions tab.** Computes realized (closed-today) + live unrealized P&L via `livePositionPnl`, exactly like the desktop `PnLCard`; shows the book label. Isolated component so price-tick re-renders stay local. Used by: MobileShell.
- `MobileSignals.tsx` — Signals tab: a **strategy dropdown** (`scannerStrategy`, options derived from the loaded signals' `strategy_name` + "All strategies") + CONF slider + Executed/Expired toggles (all reuse persisted `scanner*` store keys; the strategy filter is shared with the desktop `ScannerPanel`). Card list of today's signals, fetched on mount + polled every 15s + on `refreshKey`, **merged with live WS signals from the store** (store wins on shared ids; `update_count` carried over since WS payloads omit it). Collapsed glance: direction badge + symbol (left); compact "↻" badge (when `update_count > 0`, title-tooltip) + status badge + chevron (right) — the `↻` is icon-only (no "Updated" text) to leave room so the strike·strategy·time line doesn't truncate; then Entry/Target/SL/Conf cells. Expand (top→bottom): a **+ Watch** button placed right below the glance strip (so it stays reachable regardless of how many dedup-history rows follow; adds to the personal watchlist via `addToPersonalWatchlist`), then AI action/summary/rationale, supports/risks (full-width stacked), `ConfidenceFactors` bar, blocked reason, `SignalHistoryPanel`. (Signals carry no lots — sized at execution — so lots is omitted from the glance.)
- `MobilePositions.tsx` — Positions tab: source selector (Manual / active YOLO profiles / Shadow via `dashboardViewMode`). Mirrors `ActivePositions` source-filter + live-P&L math. Open position cards (glance Entry/LTP/SL-dist/Tgt; expand: opened, fill latency, strategy, expiry, lots/qty, SL, target, margin, conf, then a **+ Watch** button (`addToPersonalWatchlist`) beside the Close button) and a Closed-Today section with expandable trade cards (which also carry a **+ Watch** button). Polls shadow data every 30s in shadow mode.
- `MobileWatchlist.tsx` — Watchlist tab with three pill views: **Personal · Screened · Pinned** (the active pill is the persisted `watchlistFilter`, default Personal). **Personal** = the dashboard `/api/v1/watchlist` (5 indices + custom items) rendered as price rows (LTP + change%), with a top-placed `SymbolSearchInput direction="down"` to add any symbol and × to remove — shares the `watchlistItems` store slice with the desktop dashboard Watchlist. **Screened / Pinned** = the S5 intraday screener as expandable cards (`getIntradayFuturesWatchlist`), split by `item.manual` (Pinned = permanently-pinned), with Score/RS sort. Screener card: collapsed symbol + bias pill (+`•` for gap override) + LLM-confidence chip + price/change + score + RS/ADR/ORB/Gap/News strip; expand: `llm_reason`, factor grid, news, and a **+ Watch** button (adds the stock's futures to the personal watchlist via `addToPersonalWatchlist`). **Every view opens the chart**: tap a Personal row, or a screener card's symbol name, → `onOpenChart` (→ `MobileChartModal`). Takes `onOpenChart` from `MobileShell`. Polls screener 30s + personal prices 15s.
- `MobileStatusBar.tsx` — Thin strip under the mobile header: market open/closed (+ DEAD ZONE), live NIFTY intraday bias (BULLISH/BEARISH/NEUTRAL + strength, colored), and India VIX — the phone equivalent of the desktop `Header` top bar. Owns the `getMarketStatus` poll (15s) for mobile (the desktop Header isn't rendered on phones); `intradayBias` is hydrated once on mount via `api.getIntradayBias()` (persisted across refresh + shown after close) and kept live via WS `market:bias_update`. Isolated so its poll/bias ticks re-render only this strip. Used by: MobileShell.
- `MobileChartModal.tsx` — Full-screen chart overlay (routes can't render a chart page on phones). Reuses the desktop `<PriceChart fullHeight />` driven by the store's `selectedSymbol` (set on open). Pinned `data-theme="dark"` so it's an intentional dark chart sheet regardless of the mobile theme (a light chart theme is the separate full-app task). Header shows the symbol + ✕ close (Escape also closes). Used by: MobileShell.
- `MobileTrades.tsx` — Trades tab: period pills (Today / Yesterday / Week / 30 Days / Custom with date inputs — persisted via `mobileTradesPeriod` + `mobileTradesCustomStart/End`) + source selector (Manual / profiles / Shadow via `tradesViewMode`). Fetches `getTrades({status: "CLOSED", …})` only — closed-trade ledger, no "+ Open". Summary strip (net P&L respecting `showNetPnL`, win rate, count) computed client-side; the P&L sum **`Number()`-coerces `pnl`/`net_pnl`** because the backend may serialize them as Decimal strings — a bare `+` would string-concatenate and render ₹0.00 (the per-trade cards look fine since `formatINR` coerces). Same coercion in `MobilePositions`' Closed-Today total. Expandable trade cards (glance: dir, symbol, strike, strategy, P&L, entry→exit, lots, date, exit reason; expand: side, SL/Tgt, net P&L, charges, margin, source, conf, P&L %, exit time, AI summary).

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
- `api.getIntradayBias(symbol?)` — `GET /api/v1/market/intraday-bias` → `IntradayBias | null` (cached NIFTY bias, 24h TTL). Hydrates the bias on cold load + keeps it shown after close. Used by: Header, MobileStatusBar
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
- `api.getSignals(params?)` — `GET /api/v1/signals` with `status`, `generated_since/until`, `strategy`, `limit`. Each signal includes `update_count` (signal_history versions; >0 ⇒ deduped/revised). Used by: signals/page, dashboard/page
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
- `api.updateYoloProfile(id, data)` — `PATCH /api/v1/yolo-profiles/{id}`. `data` may include `invalidation_persist` (0 disables), `invalidation_quorum`, `invalidation_strong_only`. Used by: settings/page
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

#### lib/watchlistAdd.ts
- `addToPersonalWatchlist({fyersSymbol, symbol, optionType, strikePrice, expiryDate})` — resolves a Fyers symbol (searching via `api.searchSymbols` when one isn't known) and adds it to the personal watchlist (`api.addToWatchlist` + store `addWatchlistItem`). Returns `true` on success. Single source of truth for the mobile "+ Watch" buttons; mirrors the desktop ScannerPanel / ActivePositions inline logic. Used by: MobileSignals, MobilePositions.

#### lib/positionPnl.ts
Shared open-position P&L math (used by `ActivePositions`, `PnLCard`, `MobilePositions`, `MobilePnlPill` — single source of truth).
- `isShortPosition(pos)` — true when the position profits as price falls. Derived primarily from the backend's `unrealized_pnl` sign vs its last price move (immune to a stale `target_price` from partial WS `position:update`s); falls back to SL/target geometry (`target<entry` else `sl>entry`) — the same rule the backend uses, which correctly handles bought options (long), **sold options (short)**, and long/short futures. Fixes a class of bugs where a long PE/CE inverted to −ve when its premium rose.
- `livePositionPnl(pos, prices)` — returns `{currentPrice, pnl, pnlPct, slDistance, isShort}` from the freshest price (`prices[fyers_option_symbol||symbol]?.ltp` → backend `current_price`), direction via `isShortPosition`.

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
- `YoloProfile` — `{id, name, profit_cap, is_active, sort_order, is_capped_today?, invalidation_persist?: number|null, invalidation_quorum?, invalidation_strong_only?}` (the `invalidation_*` fields drive the S5 thesis-invalidation exit; persist null/0 = disabled)
- `Position` — open position; key fields: `is_shadow`, `signal_confidence`, `signal_generated_at`, `margin_required`, `fyers_option_symbol`, `position_type`, `is_permanent_watchlist`, `yolo_profile_id: string | null`
- `Trade` — closed/open trade; key fields: `source` (`MANUAL|YOLO|SHADOW`), `charges_json` (brokerage/STT/exchange/GST/SEBI/stamp/total), `net_pnl`, `margin_required`, `is_permanent_watchlist`, `yolo_profile_id: string | null`, `signal_confidence/ai_action/ai_summary/instrument_type/signal_type` (snapshotted columns), `signal_snapshot` (full JSONB), `signal_is_permanent_watchlist`
- `Signal` — trading opportunity; key fields: `signal_type` (`BUY_CE|BUY_PE|BUY_FUT|SELL_FUT`), `instrument_type` (`OPTION|FUTURE|EQUITY`), `confidence`, `is_permanent_watchlist`, `ai_summary/rationale/adjustment/action`, `update_count` (signal_history versions; >0 ⇒ deduped/revised — drives the "↻ UPD" badge on signal cards, web + mobile); **no `lots` or `quantity` fields** (resolved at execution via preview endpoint)
- `SignalHistory` — Case-2 snapshot; `version`, `entry_price/stop_loss/target_price`, `confidence`, `ai_*` fields, `captured_at`
- `SignalPreview` — from `GET /signals/{id}/preview`: `{lots, quantity, lot_size, entry_price, stop_loss, target_price, risk, notional, margin_required, sizing_meta, warnings[]}`
- `RiskDashboard` — `{capital, daily_pnl, closed_pnl, daily_drawdown_pct, max_daily_drawdown_pct, trades_today, max_trades_per_day, notional, risk, margin_utilized, is_halted, is_profit_capped, positions_open, profiles: Array<{id, name, profit_cap, current_pnl, is_capped}>}`
- `MarketStatus` — `{is_open, in_trading_window, in_dead_zone, minutes_to_close, india_vix, cpr_type, day_bias, fyers_connected}`
- `IntradayBias` — `{symbol, bias, strength, score, updated_at}` — NIFTY index intraday bias shown in the desktop Header / mobile status bar. Arrives live via WS `market:bias_update`; hydrated on cold load via `api.getIntradayBias()`; persisted in the store so it survives refresh and stays visible after close
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

#### hooks/useIsMobile.ts
`useIsMobile(breakpoint = 768)` — SSR-safe viewport hook via `matchMedia`. Returns `null` until mounted, then a boolean tracking whether the viewport is `≤ breakpoint - 1` px wide. Used by: AppShell (desktop vs `MobileShell` branch).

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
- `mobileTheme: "dark" | "light"` + `setMobileTheme` — mobile-only theme flag (desktop is always dark). Read by MobileShell to set `data-theme` on its root. Default: `"dark"`
- `mobileTab: "signals" | "positions" | "watchlist" | "trades"` + `setMobileTab` — active mobile bottom-nav tab. Persisted so a refresh restores it. Default: `"signals"`
- `watchlistFilter` (`"personal"|"screened"|"pinned"`) + `setWatchlistFilter` — mobile Watchlist active pill. Persisted. Default `"personal"`
- `mobileTradesPeriod` (`"today"|"yesterday"|"week"|"30d"|"custom"`) + `mobileTradesCustomStart`/`mobileTradesCustomEnd` + `setMobileTradesPeriod`/`setMobileTradesCustom` — mobile Trades day selector. Persisted. Default `"today"`

**Persisted keys** (via `partialize`):
- `scanLogs`, `activeTimeframe`, `dashboardViewMode` (string — `"MANUAL"` | UUID | `"SHADOW"`), `tradesViewMode` (same values), `showNetPnL`
- `tradesShowOpen`, `tradesExcludePinned`, `tradesPeriodLabel`, `tradesPeriodStart`, `tradesPeriodEnd`, `tradesStrategy`, `tradesSimOpen`, `tradesSim`, `tradesHoldOpen`, `tradesHold: { scenario: "best"|"worst"|"eod"|"sl_tgt" }`
- `scannerShowExecuted`, `scannerShowExpired`, `scannerMinConfidence`, `scannerStrategy` (`""` = all; shared by mobile Signals tab + desktop ScannerPanel)
- `signalsMinConfidence`, `signalsPeriodLabel`, `signalsPeriodStart`, `signalsPeriodEnd`, `signalsStrategy`, `signalsHideInformational`
- `positionsMinConfidence`
- `intradayBias` (last NIFTY bias — persisted so it survives refresh and stays shown after market close)
- `mobileTheme` (`"dark"` | `"light"`), `mobileTab` (`"signals"`|`"positions"`|`"watchlist"`|`"trades"`), `watchlistFilter` (`"personal"`|`"screened"`|`"pinned"`), `mobileTradesPeriod` (`"today"`|`"yesterday"`|`"week"`|`"30d"`|`"custom"`) + `mobileTradesCustomStart`/`mobileTradesCustomEnd`

**Key store actions:**
- `setTradesPeriod(label, start, end)` — updates period label + ISO strings
- `setTradesSim(partialUpdates)` / `resetTradesSim()` — sim filter state
- `setTradesHoldOpen(open)` / `setTradesHold(updates)` / `resetTradesHold()` — hold analysis panel state
- `setTradesExcludePinned(v)` — "− Pinned" toggle on trades page (sends `exclude_permanent=true` to API)
- `setScannerShowExecuted(v)` — when true, dashboard fetches EXECUTED signals alongside PENDING (re-fetch keyed on this flag)
- `setScannerShowExpired(v)` — when true, dashboard fetches EXPIRED signals alongside PENDING (re-fetch keyed on this flag)
- `setPositionsMinConfidence(v)` — shared by ActivePositions slider and PnLCard

---

### `src/__tests__/` - Unit Tests (Vitest)

Pure-logic unit tests for module-level functions that are not exported. Pattern: copy the function verbatim into the test file with a `// Copied from …` comment so the test is self-contained and refactoring doesn't silently break it.

_(No unit tests currently — `applyHoldAnalysis.test.ts` was removed when `applyHoldAnalysis()` was deleted; hold P&L is now computed on the backend.)_

---

## Theme — Institutional Terminal (Dark) + Mobile Light

Bloomberg-inspired hedge fund terminal aesthetic. Dense, monospace-forward, warm amber accent. Dark is the desktop/whole-app default; the mobile shell also ships a Kite-crisp light theme.

### Theme mechanism (ONE for the whole app)
All color decisions live in CSS variables in `globals.css`, switched by a single `data-theme` attribute on a root element:
- **Light is the semantic default** — declared on `:root` *and re-asserted* under `[data-theme="light"]` (`:root, [data-theme="light"] { … }`). The re-assert is required so a light subtree nested inside the dark-default app re-resolves the tokens (`color`/`background` inherit as computed values, so the attribute must restate them on the subtree root).
- **Dark** lives under `[data-theme="dark"]`.
- Today: `layout.tsx` sets `data-theme="dark"` on `<html>` (whole app dark → desktop byte-for-byte unchanged); `MobileShell` flips its own root to `"light"`/`"dark"` from the persisted `mobileTheme`. **Later: a whole-app toggle just sets `data-theme` on AppShell — no other wiring needed.**
- `@theme inline` maps every `--<token>` → a Tailwind utility (`bg-bg-primary`, `text-text-primary`, `text-profit`, …), so ~1,989 semantic-token usages auto-flip with the attribute.

### Palettes
```
TOKEN            DARK [data-theme=dark]      LIGHT :root / [data-theme=light]
--bg-primary     #06060b  page              #f2f3f5  light-grey page / toolbars
--bg-secondary   #0b0b13  surface           #ffffff  white cards / header / nav / drawer
--bg-tertiary    #12121c  fills/pills        #e5e8ee  inactive pills / chips (grey, filled)
--bg-elevated    #181825                     #eef0f4
--border         #1a1a2a                     #d9dce3
--border-hover   #28283e                     #c4c8d1
--text-primary   #c8c8d4                     #14161c  near-black (symbols, key values)
--text-secondary #6a6a82                     #3f4655
--text-muted     #3c3c54                     #667085  (darkened for readability on white)
--profit         #00e68a                     #07924f  bold green
--loss           #ff4060                     #d92d20  bold red
--accent         #d4a843                     #b07d10  warm gold
--warning        #f59e0b                     #c2410c
--shadow         #c084fc (= purple-400)      #7c3aed  Shadow-book violet (text/accent)
--shadow-bg      #a855f7 (= purple-500)      #7c3aed  Shadow-book fill/border (used at low opacity)
Font: Geist Sans + Geist Mono (both themes)
```
Light establishes a clear elevation hierarchy: **page (grey) < card (white) < pill** — white surfaces lift off the grey page via border + a light-only shadow.

### Tokenizing non-auto-flip colors
Hardcoded Tailwind palette colors do NOT flip with `data-theme` — tokenize them. The **Shadow-book** violet (`text-purple-400` / `bg-purple-500/15`, ~30 desktop uses) maps to `--shadow`/`--shadow-bg`, exposed as the `shadowbook` / `shadowbook-bg` utilities — named without a trailing dash after "shadow" to avoid Tailwind's `text-shadow-*` utility namespace. Usage: `text-shadowbook bg-shadowbook-bg/15 border-shadowbook-bg/30` (the mobile Shadow source pills use this; the dark values are byte-for-byte equal to the old `purple-400`/`purple-500` hardcodes). **Full-app TODO**: swap the ~30 desktop purple usages + stray `text-white`/`red-400`/`green-400`/`amber-400` to tokens; `bg-black/*` scrims are fine as-is.

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
- `.noise-bg` — subtle fractal noise texture overlay (opacity 0.015). **Disabled in light** (`[data-theme="light"].noise-bg::before { display:none }`) so the page stays clean.
- `.glow-profit` / `.glow-loss` / `.glow-accent` — colored box-shadow glows
- `.animate-fade-in` — 200ms fade-in with translateY for expanded panels
- **Light-only bar lift**: `[data-theme="light"] > header` / `> nav` get a faint `box-shadow` so the white app bars separate from the grey content column (scoped to the light subtree's direct children → dark untouched)

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
