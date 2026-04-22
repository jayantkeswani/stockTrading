"use client";

const REC_COLORS: Record<string, { bg: string; text: string }> = {
  BUY: { bg: "bg-[#00e68a]/15", text: "text-[#00e68a]" },
  HOLD: { bg: "bg-accent/15", text: "text-accent" },
  SELL: { bg: "bg-[#ff4060]/15", text: "text-[#ff4060]" },
  AVOID: { bg: "bg-[#ff4060]/15", text: "text-[#ff4060]" },
};

export function RecommendationBadge({
  recommendation,
  confidence,
}: {
  recommendation: string | null;
  confidence: number | null;
}) {
  if (!recommendation) return null;
  const colors = REC_COLORS[recommendation] || REC_COLORS.HOLD;

  return (
    <div className="flex items-center gap-3">
      <span
        className={`${colors.bg} ${colors.text} px-2 py-0.5 rounded text-xs font-mono font-bold`}
      >
        {recommendation}
      </span>
      {confidence !== null && (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-mono text-text-muted uppercase">
            Confidence
          </span>
          <span className="text-xs font-mono text-text-primary font-medium">
            {confidence.toFixed(0)}/100
          </span>
          <div className="w-16 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${
                confidence >= 70
                  ? "bg-[#00e68a]"
                  : confidence >= 50
                    ? "bg-accent"
                    : "bg-[#ff4060]"
              }`}
              style={{ width: `${Math.min(confidence, 100)}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
