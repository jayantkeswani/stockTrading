"""Per-minute order-flow aggregates for the IH v2 learning dataset (in-memory).

Fed from `feed_manager.process_tick` (every tick, but a no-op unless the symbol is tracked) and,
behind `IH_V2_DEPTH_ENABLED`, from 5-level DepthUpdate messages routed by `fyers_ws_client`.
Tracked symbols (set daily by `capture.py`): the index futures + the ATM CE/PE per index.

Per symbol per minute (IST HH:MM):
  buy_sell_imbalance  mean of (tot_buy_qty - tot_sell_qty) / (tot_buy_qty + tot_sell_qty)
  avg_spread          mean of (ask - bid) when both quotes are valid
  size_imbalance      mean of (bid_size - ask_size) / (bid_size + ask_size)
  tick_rule_delta     sum of traded-volume deltas signed by the tick rule (uptick +, downtick -,
                      unchanged price carries the previous sign)
  depth_imbalance     mean of (sum bid sizes - sum ask sizes) / total over the 5 levels (depth on)
Field names are the Fyers SDK's (`map.json` data_val / depthvalue). Never raises into the feed.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from app.core.constants import IST

_KEEP_MINUTES = 240


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


class _Bucket:
    __slots__ = ("n", "imb_sum", "imb_n", "spread_sum", "spread_n", "size_sum", "size_n",
                 "delta", "depth_sum", "depth_n")

    def __init__(self) -> None:
        self.n = 0
        self.imb_sum = self.spread_sum = self.size_sum = self.delta = self.depth_sum = 0.0
        self.imb_n = self.spread_n = self.size_n = self.depth_n = 0

    def as_dict(self) -> dict:
        def _avg(s, n):
            return round(s / n, 6) if n else None
        return {
            "ticks": self.n,
            "buy_sell_imbalance": _avg(self.imb_sum, self.imb_n),
            "avg_spread": _avg(self.spread_sum, self.spread_n),
            "size_imbalance": _avg(self.size_sum, self.size_n),
            "tick_rule_delta": round(self.delta, 2),
            "depth_imbalance": _avg(self.depth_sum, self.depth_n),
        }


class OrderFlowTracker:
    """Accumulates per-(symbol, minute) order-flow aggregates for tracked symbols."""

    def __init__(self) -> None:
        self._tracked: set[str] = set()
        self._buckets: dict[str, OrderedDict[str, _Bucket]] = {}
        self._last_ltp: dict[str, float] = {}
        self._last_vol: dict[str, float] = {}
        self._last_sign: dict[str, int] = {}

    @property
    def tracked(self) -> set[str]:
        return set(self._tracked)

    def track(self, symbols) -> None:
        """Add symbols (internal names as they arrive in ticks) to the tracked set."""
        self._tracked.update(s for s in symbols if s)

    def reset(self) -> None:
        """Forget everything (day roll / tests)."""
        self._tracked.clear()
        self._buckets.clear()
        self._last_ltp.clear()
        self._last_vol.clear()
        self._last_sign.clear()

    def _bucket(self, symbol: str, ts: datetime | None) -> _Bucket:
        key = (ts or datetime.now(IST)).astimezone(IST).strftime("%H:%M")
        per = self._buckets.setdefault(symbol, OrderedDict())
        b = per.get(key)
        if b is None:
            b = per[key] = _Bucket()
            while len(per) > _KEEP_MINUTES:
                per.popitem(last=False)
        return b

    def on_tick(self, symbol: str, tick: dict, ts: datetime | None = None) -> None:
        """Feed one SymbolUpdate tick (the tick_data dict built in fyers_ws_client)."""
        if symbol not in self._tracked:
            return
        b = self._bucket(symbol, ts)
        b.n += 1
        tb, ts_ = _num(tick.get("tot_buy_qty")), _num(tick.get("tot_sell_qty"))
        if tb is not None and ts_ is not None and tb + ts_ > 0:
            b.imb_sum += (tb - ts_) / (tb + ts_)
            b.imb_n += 1
        bid, ask = _num(tick.get("bid")), _num(tick.get("ask"))
        if bid and ask and bid > 0 and ask >= bid:
            b.spread_sum += ask - bid
            b.spread_n += 1
        bs, as_ = _num(tick.get("bid_size")), _num(tick.get("ask_size"))
        if bs is not None and as_ is not None and bs + as_ > 0:
            b.size_sum += (bs - as_) / (bs + as_)
            b.size_n += 1
        ltp, vol = _num(tick.get("ltp")), _num(tick.get("volume"))
        if ltp and ltp > 0 and vol is not None:
            prev_ltp, prev_vol = self._last_ltp.get(symbol), self._last_vol.get(symbol)
            if prev_ltp is not None and prev_vol is not None and vol >= prev_vol:
                sign = 1 if ltp > prev_ltp else (-1 if ltp < prev_ltp else self._last_sign.get(symbol, 0))
                self._last_sign[symbol] = sign
                b.delta += sign * (vol - prev_vol)
            self._last_ltp[symbol], self._last_vol[symbol] = ltp, vol

    def on_depth(self, symbol: str, depth: dict, ts: datetime | None = None) -> None:
        """Feed one 5-level DepthUpdate message (runs as an event-loop callback; never raises)."""
        if symbol not in self._tracked:
            return
        try:
            bids = sum(_num(depth.get(f"bid_size{i}")) or 0 for i in range(1, 6))
            asks = sum(_num(depth.get(f"ask_size{i}")) or 0 for i in range(1, 6))
            if bids + asks > 0:
                b = self._bucket(symbol, ts)
                b.depth_sum += (bids - asks) / (bids + asks)
                b.depth_n += 1
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self, symbol: str, minute: str) -> dict | None:
        """Aggregates for `symbol` in minute 'HH:MM', or None if no ticks were seen."""
        b = self._buckets.get(symbol, {}).get(minute)
        return b.as_dict() if b else None


orderflow_tracker = OrderFlowTracker()
