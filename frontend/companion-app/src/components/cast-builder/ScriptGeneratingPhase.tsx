import { useEffect, useState } from "react";
import { Loader2, Wand2 } from "lucide-react";

// There's no real progress signal for outline+script generation (unlike
// audio, which reports generation_progress) — this is a bounded interstitial
// rather than a live-polled bar, cycling through steps on a timer so the
// user sees the wait is understood/expected instead of a bare toast with a
// silent 15-30s gap before an unexplained tab switch.
const STEPS = [
  "Analyzing your product...",
  "Planning the script structure...",
  "Writing your script...",
  "Almost done...",
];

export function ScriptGeneratingPhase() {
  const [stepIdx, setStepIdx] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setStepIdx((i) => Math.min(i + 1, STEPS.length - 1));
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="max-w-lg mx-auto p-6 flex flex-col items-center justify-center min-h-[400px] space-y-6">
      <div className="w-16 h-16 rounded-full bg-accent/20 flex items-center justify-center">
        <Wand2 className="w-8 h-8 text-accent animate-pulse" />
      </div>
      <div className="text-center space-y-2">
        <h2 className="text-lg font-semibold text-white">Generating Script</h2>
        <p className="text-sm text-white/50">{STEPS[stepIdx]}</p>
      </div>
      <div className="flex items-center gap-2 text-white/40 text-xs">
        <Loader2 className="w-3 h-3 animate-spin" />
        This usually takes 15-30 seconds
      </div>
    </div>
  );
}
