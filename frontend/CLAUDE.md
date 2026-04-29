# Frontend - Next.js / React / TypeScript

## Tech Stack
- Next.js 15 (App Router)
- TypeScript (strict mode)
- Tailwind CSS v4 (dark theme only — institutional terminal aesthetic)
- Zustand for state management
- TradingView lightweight-charts for price charts
- WebSocket for real-time updates from backend on port 8080

## Module Map

### `src/app/` - Pages (App Router)
All pages use `'use client'` directive.
- `page.tsx` - Dashboard: full-width PnL strip at top, then 8-col left (ScannerHeader, ScannerPanel, ActivePositions) + 4-col right (Watchlist, ScanFeed, AgentFeed). Loads pending signals and open positions from DB on mount. ChartModal overlay.
- `layout.tsx` - Root layout with AppShell wrapper
- `trades/page.tsx` - Kite-style P&L dashboard. Period filter pills (Today/Week/Month/3M/Custom, default=current month), SummaryStrip (total P&L, win rate, best/worst day), PnLHeatmap (calendar grid with cell intensity by daily P&L), filterable TradesTable. **Real / Signal Test toggle**: default "Real" shows only `source != SHADOW` trades; "Signal Test" fetches `?source=SHADOW` and shows an info banner. Clicking a heatmap cell filters the table to that day.
- `signals/page.tsx` - Signal history feed with status badges
- `settings/page.tsx` - Strategy configuration (is_active, auto_mode, symbols per strategy with autocomplete + group presets), risk parameters. Wired to backend `/api/v1/strategies` endpoints. When a symbol is added via autocomplete, the full Fyers symbol is sent alongside (`symbol_map`) so the backend never needs to reconstruct it. Group-add symbols are auto-resolved server-side via the symbol master. **StrategyParams component**: collapsible section per strategy showing numeric inputs for tunable parameters (loaded via `getParameterDefaults`); values are saved to `strategy_configs.parameters` JSONB on submit.
- `research/page.tsx` - AI Research: symbol search, real-time agent progress, full report with expandable cards, past reports history
- `agent/page.tsx` - Agent dashboard: YOLO toggle, autonomy level badge, 5-card status grid, action logs
- `chart/page.tsx` - Full TradingView chart page. Symbol tab row (all 5 indices + watchlist items, horizontally scrollable). Fetches watchlist on mount to populate extra tabs.
- `intraday-futures/page.tsx` - Strategy 5 dedicated page. Full-width DayStatusBar at top. Two-column layout: 8-col left (Watchlist), 4-col right (AgentLog, SetupPerformance, GlobalCues). Polls phase/stats every 5s, agent log every 10s, loads watchlist/briefing/cues on mount.

### `src/components/` - React Components (by domain)

**layout/**
- `AppShell.tsx` - Main wrapper: sidebar (48px) + header (36px) + content area. Applies `noise-bg` texture class.
- `Header.tsx` - Thin status bar (36px): IST clock, market status, VIX, Tasks/Fyers/Agent/WS indicators with monospace labels. Polls `getAllPrices()` + market status every 10s.
- `TasksPopup.tsx` - Background tasks popup (click-to-open). Shows all registered tasks with status dots, type badges (scheduler/startup/service), schedule/description metadata, timestamps, errors. Polls `GET /api/v1/tasks` every 5s while open. Click-outside to close.
- `AgentPopup.tsx` - Agent control popup (click-to-open). Start/stop agent, toggle SEMI/YOLO mode. Shows positions monitored, pending confirmations, uptime. Reads from Zustand store, writes via API. Click-outside to close.
- `Sidebar.tsx` - Narrow icon rail (48px): page links with hover tooltips, paper trading indicator

**charts/**
- `PriceChart.tsx` - TradingView candlestick chart with real data from Fyers (via backend proxy `GET /ohlcv`). Supports 1m/5m/15m/1h/1D timeframes. **Active timeframe** stored in Zustand (`activeTimeframe`). **Refresh button** in header re-fetches OHLCV without recreating the chart (separate data-load effect keyed by `refreshKey`). **Live updates**: watches `prices[selectedSymbol]` from the store and updates the current candle via `series.update()`. Creates new candles when IST-aligned bucket time advances. **IST display**: backend timestamps are fed as UTC unix seconds — do NOT shift them (library won't render "future" candles). IST display is via `localization.timeFormatter`: adds `IST_OFFSET` (19800s) for axis labels and crosshair only. `currentBucketTime()` aligns bucket boundaries in IST then returns UTC (`- IST_OFFSET`). **Last candle always visible**: no save/restore of logical range (stale ranges hide newly added candles); always calls `fitContent()` after data loads. `timeScale.rightOffset: 5` reserves 5 bars of space so the last candle is never clipped at the edge. **OHLC legend**: `subscribeCrosshairMove` populates an `OHLCInfo` state overlay rendered top-left of the chart — shows timestamp (IST), O/H/L/C, candle % change, and volume (formatted K/L/Cr). Falls back to last candle values when crosshair leaves. `lastVolRef` tracks the last-loaded candle's volume for the fallback. Helper functions `fmtPrice`, `fmtVol`, `fmtTimeIST` are module-level pure functions. Three-effect architecture: Effect 1 creates/destroys chart + subscribes crosshair; Effect 2 loads data; Effect 3 handles live ticks.
- `ChartModal.tsx` - Modal wrapper (90vw × 85vh) for detailed chart view. Symbol tab row shows all 5 default indices plus any custom watchlist items (fetched via `api.getWatchlist()` when modal opens, horizontally scrollable). Pop-out opens `/chart?symbol=…` in a new tab.

**dashboard/**
- `PnLCard.tsx` - Single-line horizontal strip: P&L, drawdown (with thin bar), trades count, capital at risk — all inline with dividers. **Ghost mode**: reads `positionViewMode` from store; when SHADOW, computes P&L from `shadowPositions` + `shadowClosedToday` (client-side), shows `GHOST` pill, hides drawdown bar, purple border tint. Real mode: unchanged — combines `closed_pnl` from risk endpoint with live unrealized from store. Halted badge inline (real mode only).
- `Watchlist.tsx` - Uniform symbol list (no visual distinction between default indices and custom items). Debounced search (300ms, min 1 char), dropdown autocomplete from `/symbols/search` (local symbol master, supports substring matching). Custom items stored in backend Redis via `/api/v1/watchlist`. Supports stocks, futures, options with segment badges (FUT/OPT). Max-height 300px with scroll. Subscribes custom symbols on WebSocket (on mount + when adding) for real-time price ticks.
- `ScannerHeader.tsx` - Ultra-compact strategy pill bar. Monospace text-only buttons (no icons). Triggers manual batch evaluation via `POST /api/v1/strategies/evaluate/batch`. Logs start/end entries to ScanFeed.
- `ScannerPanel.tsx` - Structured signal cards with **strategy-aware rendering**. Each card has three sections: (1) header — direction arrow, symbol label, timestamp, confidence, strategy badge; (2) prices — entry, SL, target, R:R, plus strategy-specific context; (3) actions — EXEC button (opens `ExecuteSignalModal`), "+ Watch" watchlist button, **▼ Details** expander (shows raw reason string), **AI** button (shows LLM overlay panel), dismiss. Details and AI panels are mutually exclusive toggles. **Strategy-specific context rows**: Strategy 5 (`intraday_futures`) shows setup_type badge, enhanced ORB badge, phase, RVOL (color-coded), VWAP, ORB range, PDH/PDL, gap%, risk_warnings; Strategy 2 shows VWAP distance, OI, index entry (unchanged). **AI panel** shows: `ai_summary` one-liner + `ai_adjustment` badge, `ai_rationale` paragraph, key supports/risks lists from `indicators.ai_key_supports`/`ai_key_risks`, and confidence factor bar chart from `indicators.confidence_factors` with strategy-specific label maps (`S5_CONFIDENCE_FACTOR_LABELS` for Strategy 5's 8 factors). AI button shows `✦ AI` (amber) when `ai_summary` is present, plain `AI` when not. If no AI data, panel shows "No AI analysis yet" placeholder. **isBullish** logic is direction-aware: BUY/BUY_FUT = bullish, SELL/SELL_FUT = bearish. **`signal.indicators` is cast as `(signal.indicators ?? {})` — the null guard is required because pre-Phase-2 signals or non-VWAP strategies may have a null indicators field, which would crash the card.**
- `ExecuteSignalModal.tsx` - Pre-trade confirm modal. Fetches `GET /signals/{id}/preview` for live entry price + computed lots. Shows lot stepper (editable), quantity, capital at risk. Submits `POST /signals/{id}/execute` with optional lots override. YOLO path is unaffected (no modal).
- `ScanFeed.tsx` - Compact scan log. Shows scan start/end messages with stats. Max-height 160px.
- `AgentFeed.tsx` - Agent action log as flat list (not nested). Filterable by strategy and action type via dropdowns. Each row: timestamp, action type, symbol, strategy chip badge, P&L. Max-height 300px. `actionTypes` derived set guards against `undefined` (logs with missing `action_type` are excluded from the filter dropdown).

**research/**
- `ResearchSearch.tsx` - Symbol autocomplete (reuses `searchSymbols` API, filters to EQ + INDEX), debounced 300ms, "Analyze" button. Dropdown shows equity and index matches.
- `ResearchProgress.tsx` - Live agent status tracker: 6 agents shown as dots (pending/running/done/failed) with labels and durations. Updates in real-time from WebSocket `research:*` events.
- `ResearchReport.tsx` - Full report display: header with recommendation badge + confidence meter, executive summary, ActionableLevels card, risks/catalysts, expandable section cards (Fundamental, Technical, OI, Institutional, News, Valuation). All LLM-generated text (executive summary, risks, catalysts, section summaries) rendered via `<Markdown>` component. News section shows articles with source URLs.
- `Markdown.tsx` - Thin `react-markdown` + `remark-gfm` wrapper with theme-matched component overrides (monospace, dark theme tokens, amber accents). Used for all LLM-generated text in `ResearchReport`.
- `ReportHistory.tsx` - Past reports list with symbol, recommendation badge, confidence, and relative time.
- `ActionableLevels.tsx` - Visual card showing entry zone, SL, T1/T2, R:R ratio, timeframe, long-term suitability.
- `RecommendationBadge.tsx` - BUY/HOLD/SELL badge (color-coded) + confidence percentage bar.

**trades/**
- `PeriodFilter.tsx` - Period pill bar (Today / Week / Month / 3M / Custom). Active pill: `bg-accent/20 text-accent border-accent/30`. Custom opens two native `<input type="date">` fields + Apply button. Emits `{ start, end, label }` upward.
- `SummaryStrip.tsx` - Inline metrics strip (mirrors PnLCard style). Shows total P&L, # closed trades (+ open count badge), win rate, best day, worst day, avg P&L/trade — all computed client-side from props.
- `PnLHeatmap.tsx` - Calendar heatmap. One month-grid per month in the period; cells colored by daily P&L intensity via inline rgba styles (profit green / loss red). Clicking a cell with trades fires `onSelectDay`; clicking the same cell again deselects. Today highlighted with amber ring.
- `TradesTable.tsx` - Extracted dense trades table. Accepts `trades: Trade[]`, `loading: boolean`, `showSource?: boolean`. When `showSource=true` (Signal Test mode), renders a purple `SHADOW` / `YOLO` / `MANUAL` pill column.

**intraday-futures/**
- `DayStatusBar.tsx` - Phase badge, agent status (ACTIVE/PAUSED/HALTED), daily stats (trades x/5, positions x/3, P&L), date picker for historical review, action buttons (Briefing, Screener, Pause/Resume — hidden in historical mode). Accepts `date`/`onDateChange` props from page. Polls phase + stats + agent status every 5s (live mode only). Historical mode queries real phase from Redis (`strat5:phase:{date}`) instead of hardcoding "DONE".
- `Watchlist.tsx` - Sortable table of Strategy 5 screener results: symbol, composite score, RS percentile, ADR%, **Gap%** (colored, with relative gap tooltip), bias badge (dot indicator when gap-overridden, tooltip shows original bias + source), LLM confidence badge, news sentiment score, **ORB** column (range width with tooltip for H/L values, populated after 9:30 AM from Redis), price. Sortable by score or RS. Uses `S5WatchlistItem` type from `types.ts`. Accepts `date` prop. Polls every 30s in live mode (was single fetch on mount).
- `AgentLog.tsx` - Reverse-chronological feed of Strategy 5 agent activity. Category badges (BRIEFING, SCREENER, ORB, SIGNAL, TRADE, EXIT, SKIP, PHASE, RISK, GLOBAL, SYSTEM) with color coding. **Category filter**: toggle pills in header (one per category found in current entries); clicking a pill filters; empty selection = show all; "clear" button resets; shows filtered/total count when active. Uses `S5AgentLogEntry` type. Accepts `date` prop. Polls every 10s (live mode only).
- `SetupPerformance.tsx` - Compact collapsible section showing per-setup win rate bars, W/L counts, and P&L. Overall summary row at bottom. Fetches via `getIntradayFuturesSetupPerformance()` on mount + date change. Uses `S5SetupPerformance` type.
- `GlobalCues.tsx` - Collapsible section: morning briefing (approach badge, sector bias, summary, flags) + global market cues (Nifty, S&P 500, Nasdaq, Dow Futures, Crude, USD/INR, DXY, US VIX with >20 warning). Uses `S5GlobalCues`/`S5MorningBriefing` types. Accepts `date` prop.
- `ConfigPanel.tsx` - Collapsible Strategy 5 parameter editor. Loads defaults via `getParameterDefaults("intraday_futures")`, merges with saved params from strategy config. Saves via `updateStrategy` API. Numeric inputs for all tunable params (trailing SL, confidence tiers, RVOL thresholds, position/trade limits).

**positions/**
- `ActivePositions.tsx` - Dense table of open positions with unrealized P&L, SL distance warnings, expandable detail rows. **Real/Ghost toggle** in header: Real mode shows live positions from store; Ghost mode fetches `/positions?include_shadow=true` (filtered to `is_shadow=true`) + shadow closed trades today every 30s — purple border tint, no CLOSE button (ghost positions auto-close via trade_monitor). **Live P/L**: computes P/L reactively from `prices` store using `pos.fyers_option_symbol || pos.symbol` as price key. Subscribes position symbols on WebSocket for real-time ticks.

### `src/hooks/` - Custom Hooks
- `useWebSocket.ts` - WebSocket connection to backend (auto-detects host via `getWsUrl()` — `ws://{window.location.hostname}:8080/ws`). Auto-reconnect on close (3s delay). Guards all event handlers (`onopen`/`onclose`/`onerror`) with `ws === wsRef.current` staleness check to prevent React Strict Mode race conditions (stale WS close event overwriting current connection state). Subscribes to all 5 index symbols on connect. Handles `signal:new`, `signal:updated` (dedup updates), `trade:open` (adds new position to store), and `research:*` events (started, agent_started, agent_completed, agent_failed, completed, failed → updates Zustand research slice). Exported `subscribeSymbols(symbols)` helper — uses module-level shared WS ref, callable from any component without needing the wsRef.

### `src/lib/` - Utilities
- `api.ts` - REST client: trades, signals, positions, agent, risk, market data, strategies, intraday-futures. `getTrades()` accepts `source?: string` — pass `"SHADOW"` to fetch shadow-only trades. `getTradeSummary(source?)` same. By default both return only non-shadow trades (backend filter). `getPositions(includeShadow = false)` — pass `true` to include shadow positions. `getClosedTradesToday(source?)` — pass `"SHADOW"` to fetch ghost closed trades. `getParameterDefaults(name)` — fetches raw default parameters for a strategy (used by StrategyParams component in settings). **Strategy 5 endpoints**: `getIntradayFuturesWatchlist`, `getIntradayFuturesAgentLog`, `getIntradayFuturesGlobalCues`, `getIntradayFuturesBriefing`, `getIntradayFuturesDailyStats`, `getIntradayFuturesPhase` (accepts optional `date` param for historical phase lookup), `getIntradayFuturesAgentStatus`, `getIntradayFuturesSetupPerformance(date?, days?)` (per-setup win rate/P&L), `runIntradayFuturesScreener`, `runIntradayFuturesBriefing`, `setIntradayFuturesAgentAction` — all accept optional `date` param (defaults to today).
- `types.ts` - TypeScript interfaces for all entities. `Trade` has `source: "MANUAL" | "YOLO" | "SHADOW"`. `Position` has `is_shadow: boolean`. `S5WatchlistItem` includes `orb_high?`, `orb_low?`, `orb_range?` fields. `S5SetupStats` and `S5SetupPerformance` for per-setup performance tracking.
- `formatters.ts` - INR currency (Indian number system: lakhs/crores), percentages, IST datetime. All formatters coerce inputs via `Number()` to handle string Decimals from the backend. IST date helpers: `startOfDayIST`, `endOfDayIST`, `startOfMonthIST`, `endOfMonthIST`, `startOfWeekIST`, `subDaysIST`, `subMonthsIST`, `eachDayInRange`, `isoDateIST` (YYYY-MM-DD), `formatDateShort` ("24 Apr"), `monthLabel` ("April 2026"), `toISTDate` (Date → IST-adjusted Date).
- `constants.ts` - `SYMBOLS` (5 indices), `STRATEGY_LABELS`, `STATUS_COLORS`, `getWsUrl()` (auto-detects host: uses `window.location.hostname` when accessed remotely, falls back to `ws://localhost:8080/ws`), `Timeframe` type ("1m" | "5m" | "15m" | "1h" | "1D")

### `src/store/` - Zustand State
- `index.ts` - Single store with slices: prices (per symbol), positions, signals, scan logs, risk metrics, agent status, market status, UI state. Position `addPosition()` skips shadow events (`is_shadow=true`). **`closedToday: Trade[]`** slice (real trades only). **`positionViewMode: "REAL" | "SHADOW"`** — drives ActivePositions + PnLCard toggle; **`shadowPositions: Position[]`** + **`shadowClosedToday: Trade[]`** — populated by ActivePositions when Ghost mode is active. **`activeTimeframe: Timeframe`** — persisted to localStorage.

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
- **Amber accent** — warm gold (#d4a843) for active states, badges, selections — replaces generic indigo

### CSS Utilities (globals.css)
- `.noise-bg` — subtle fractal noise texture overlay (opacity 0.015)
- `.glow-profit` / `.glow-loss` / `.glow-accent` — colored box-shadow glows
- `.animate-fade-in` — 200ms fade-in with translateY for expanded panels

### Layout Dimensions
- Sidebar: 48px wide (`w-12`)
- Header: 36px tall (`h-9`)
- Main content offset: `ml-12 mt-9 p-3`

## Conventions
- All pages are client components (`'use client'`)
- All API calls go through `lib/api.ts` — never raw fetch in components
- WebSocket events go through `hooks/useWebSocket.ts`
- Currency formatted as INR with Indian number system (e.g., Rs 1,50,000)
- All 5 indices always referenced: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
- Backend API base URL: auto-detected from `window.location.hostname` (falls back to `http://localhost:8080/api/v1`)
- WebSocket URL: auto-detected from `window.location.hostname` (falls back to `ws://localhost:8080/ws`)
- `next.config.ts` sets `allowedDevOrigins: ["192.168.*.*", "100.*.*.*"]` — allows cross-machine dev access from local network and Tailscale IPs without HMR blocking
- Strategy badges: `text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent`
- Section headers: `text-xs font-mono font-medium text-text-secondary uppercase tracking-wider`
- Empty states: `text-xs font-mono text-text-muted` with lowercase text

## How-To Guides

### Add a New Page
1. Create `src/app/{page-name}/page.tsx` with `'use client'`
2. Add nav link in `src/components/layout/Sidebar.tsx`
3. Create domain components in `src/components/{domain}/`
4. Add store slice in `src/store/index.ts` if page needs its own state

### Add a New Component
1. Place in `src/components/{domain}/` matching the page domain
2. Use Tailwind with theme variables (--bg-primary, --profit, --loss, --accent, etc.)
3. Follow terminal aesthetic: monospace text, tight padding, sharp corners, uppercase headers
4. Get data from Zustand store or pass as props — don't fetch inside components
5. Use `formatINR()`, `formatPercent()` from `lib/formatters.ts`

### Add a New API Call
1. Add function in `src/lib/api.ts`
2. Add TypeScript types in `src/lib/types.ts`
3. Call from component or store action — not directly from hooks

### Add a New WebSocket Event
1. Add event type in `src/hooks/useWebSocket.ts` handler
2. Add store action in `src/store/index.ts` to process the event
3. Backend must publish to Redis channel for the event to flow through
