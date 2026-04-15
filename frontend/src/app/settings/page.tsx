"use client";

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Settings</h1>

      {/* Paper Trading Toggle */}
      <div className="rounded-lg border border-border bg-bg-secondary p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-medium">Paper Trading Mode</h2>
            <p className="text-sm text-text-secondary mt-1">
              When enabled, all trades are simulated. No real money is used.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-warning font-medium">PAPER</span>
            <div className="w-10 h-5 bg-warning/30 rounded-full relative">
              <div className="absolute left-0.5 top-0.5 w-4 h-4 bg-warning rounded-full" />
            </div>
          </div>
        </div>
      </div>

      {/* Risk Parameters */}
      <div className="rounded-lg border border-border bg-bg-secondary p-6">
        <h2 className="font-medium mb-4">Risk Management</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-text-muted block mb-1">
              Trading Capital (INR)
            </label>
            <input
              type="text"
              defaultValue="10,00,000"
              className="w-full bg-bg-tertiary border border-border rounded px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="text-xs text-text-muted block mb-1">
              Max Daily Drawdown (%)
            </label>
            <input
              type="number"
              defaultValue={5}
              className="w-full bg-bg-tertiary border border-border rounded px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="text-xs text-text-muted block mb-1">
              Risk Per Trade (%)
            </label>
            <input
              type="number"
              defaultValue={2}
              className="w-full bg-bg-tertiary border border-border rounded px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="text-xs text-text-muted block mb-1">
              Max Trades Per Day
            </label>
            <input
              type="number"
              defaultValue={3}
              className="w-full bg-bg-tertiary border border-border rounded px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none"
            />
          </div>
        </div>
      </div>

      {/* Strategy Config */}
      <div className="rounded-lg border border-border bg-bg-secondary p-6">
        <h2 className="font-medium mb-4">Active Strategies</h2>
        <div className="space-y-3">
          {[
            { name: "VWAP Pullback", key: "vwap_pullback", active: true },
            { name: "ORB (Opening Range Breakout)", key: "orb", active: false },
            { name: "Gamma Scalping (Expiry Day)", key: "gamma_scalping", active: false },
          ].map((s) => (
            <div
              key={s.key}
              className="flex items-center justify-between py-2 border-b border-border/50 last:border-0"
            >
              <div>
                <span className="text-sm">{s.name}</span>
                {!s.active && (
                  <span className="ml-2 text-xs text-text-muted">(not implemented)</span>
                )}
              </div>
              <div
                className={`w-10 h-5 rounded-full relative cursor-pointer ${
                  s.active ? "bg-profit/30" : "bg-bg-tertiary"
                }`}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 rounded-full transition-all ${
                    s.active
                      ? "left-5.5 bg-profit"
                      : "left-0.5 bg-text-muted"
                  }`}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Notification Config */}
      <div className="rounded-lg border border-border bg-bg-secondary p-6">
        <h2 className="font-medium mb-4">Notifications</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-text-muted block mb-1">
              Telegram Bot Token
            </label>
            <input
              type="password"
              placeholder="Enter bot token..."
              className="w-full bg-bg-tertiary border border-border rounded px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="text-xs text-text-muted block mb-1">
              Telegram Chat ID
            </label>
            <input
              type="text"
              placeholder="Enter chat ID..."
              className="w-full bg-bg-tertiary border border-border rounded px-3 py-2 text-sm font-mono focus:border-accent focus:outline-none"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
