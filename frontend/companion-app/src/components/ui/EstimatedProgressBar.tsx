import { useState, useEffect } from "react";

interface EstimatedProgressBarProps {
  estimatedSeconds: number;
  isComplete: boolean;
  label?: string;
}

export function EstimatedProgressBar({ estimatedSeconds, isComplete, label }: EstimatedProgressBarProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (isComplete) return;
    const interval = setInterval(() => {
      setElapsed((e) => e + 1);
    }, 1000);
    return () => clearInterval(interval);
  }, [isComplete]);

  useEffect(() => {
    if (isComplete) setElapsed(0);
  }, [isComplete]);

  const progress = isComplete
    ? 100
    : Math.min(95, (elapsed / Math.max(estimatedSeconds, 1)) * 95);
  const remaining = Math.max(0, estimatedSeconds - elapsed);

  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs text-text-muted">
        <span>{label || "Processing..."}</span>
        <span>{isComplete ? "Done" : `~${remaining}s remaining`}</span>
      </div>
      <div className="h-2 bg-surface rounded-full overflow-hidden">
        <div
          className="h-full bg-accent transition-all duration-1000 ease-linear rounded-full"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
