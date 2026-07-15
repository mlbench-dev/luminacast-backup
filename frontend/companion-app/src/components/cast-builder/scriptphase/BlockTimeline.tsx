import { cn } from "@/lib/cn";

/**
 * BlockTimeline — a subtle ruler under each block card. Reads as "this
 * block runs for N seconds". Tick marks every 1s, labels every 5s (or
 * every 1s if the block is shorter than 5s).
 *
 * Visual debt note: the parent card already shows "~Ns" in the header;
 * this ruler adds a *spatial* sense of duration, not just a number. The
 * goal is to make a 30s block feel visually heavier than a 3s block at
 * a glance, the way a CapCut storyboard does.
 */
export function BlockTimeline({
  durationSeconds,
  className,
}: {
  durationSeconds: number;
  className?: string;
}) {
  const total = Math.max(0.1, durationSeconds);
  const showEverySecond = total <= 6;
  const labelInterval = showEverySecond ? 1 : 5;

  // Generate tick positions. We always include t=0 and t=total at the
  // ends so the ruler reads as a complete span.
  const ticks: number[] = [];
  for (let t = 0; t <= total; t += 1) ticks.push(Math.min(t, total));
  if (ticks[ticks.length - 1] !== total) ticks.push(total);

  return (
    <div className={cn("relative h-5 mt-2 select-none", className)}>
      {/* Baseline */}
      <div className="absolute inset-x-0 top-0 h-px bg-white/[0.06]" />

      {ticks.map((t, i) => {
        const left = (t / total) * 100;
        const isMajor = t % labelInterval === 0 || i === 0 || i === ticks.length - 1;
        return (
          <div
            key={`${t}-${i}`}
            className="absolute top-0"
            style={{ left: `${left}%` }}
          >
            <div
              className={cn(
                "w-px",
                isMajor ? "h-2 bg-white/20" : "h-1 bg-white/10",
              )}
            />
            {isMajor && (
              <span
                className="absolute top-2.5 text-[9px] tabular-nums text-white/30 -translate-x-1/2"
              >
                {t < 1 && t > 0 ? `${t.toFixed(1)}s` : `${Math.round(t)}s`}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
