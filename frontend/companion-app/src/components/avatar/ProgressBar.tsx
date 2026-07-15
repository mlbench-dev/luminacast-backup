import { cn } from "@/lib/cn";

interface ProgressBarProps {
  percent: number;
  stepText?: string;
  failed?: boolean;
  className?: string;
}

export function ProgressBar({ percent, stepText, failed, className }: ProgressBarProps) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="h-2.5 bg-border rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-700",
            failed ? "bg-danger" : "bg-accent",
          )}
          style={{ width: `${Math.max(failed ? 100 : Math.min(percent, 100), 2)}%` }}
        />
      </div>
      <div className="flex items-center justify-between">
        {stepText && (
          <p className={cn("text-xs", failed ? "text-danger" : "text-text-muted")}>{stepText}</p>
        )}
        <span className={cn("text-xs font-medium", failed ? "text-danger" : "text-text-muted")}>
          {failed ? "Failed" : `${Math.round(percent)}%`}
        </span>
      </div>
    </div>
  );
}
