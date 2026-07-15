import { CheckCircle } from "lucide-react";
import { cn } from "@/lib/cn";

export interface StepDef {
  label: string;
  key: string;
}

interface StepIndicatorProps {
  steps: StepDef[];
  currentStep: string;
  completedSteps: string[];
  onStepClick?: (key: string) => void;
}

export function StepIndicator({ steps, currentStep, completedSteps, onStepClick }: StepIndicatorProps) {
  const currentIdx = steps.findIndex((s) => s.key === currentStep);

  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1">
      {steps.map((step, idx) => {
        const isCompleted = completedSteps.includes(step.key);
        const isCurrent = step.key === currentStep;
        const isFuture = !isCompleted && !isCurrent;
        const isClickable = isCompleted && onStepClick;

        return (
          <div key={step.key} className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => isClickable && onStepClick(step.key)}
              disabled={!isClickable}
              className={cn(
                "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-all",
                isCompleted && "bg-green-500/15 text-green-400 hover:bg-green-500/25 cursor-pointer",
                isCurrent && "bg-accent/20 text-accent ring-1 ring-accent/40",
                isFuture && "bg-border/30 text-text-muted cursor-default",
              )}
            >
              {isCompleted ? (
                <CheckCircle className="h-3.5 w-3.5" />
              ) : (
                <span className={cn(
                  "flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold",
                  isCurrent ? "bg-accent text-white" : "bg-border/50 text-text-muted",
                )}>
                  {idx + 1}
                </span>
              )}
              {step.label}
            </button>
            {idx < steps.length - 1 && (
              <div className={cn(
                "h-px w-4 shrink-0",
                idx < currentIdx ? "bg-green-500/40" : "bg-border/40",
              )} />
            )}
          </div>
        );
      })}
    </div>
  );
}
