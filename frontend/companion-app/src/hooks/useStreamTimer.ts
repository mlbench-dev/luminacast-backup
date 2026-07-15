import { useState, useEffect, useRef, useCallback } from "react";

interface UseStreamTimerOptions {
  initialSeconds?: number;
  autoStart?: boolean;
  onComplete?: () => void;
}

export function useStreamTimer({
  initialSeconds = 0,
  autoStart = false,
  onComplete,
}: UseStreamTimerOptions = {}) {
  const [seconds, setSeconds] = useState(initialSeconds);
  const [isRunning, setIsRunning] = useState(autoStart);
  const intervalRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!isRunning) return;

    intervalRef.current = window.setInterval(() => {
      setSeconds((prev) => {
        if (prev <= 1) {
          setIsRunning(false);
          onComplete?.();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(intervalRef.current);
  }, [isRunning, onComplete]);

  const start = useCallback((secs?: number) => {
    if (secs !== undefined) setSeconds(secs);
    setIsRunning(true);
  }, []);

  const pause = useCallback(() => {
    setIsRunning(false);
  }, []);

  const reset = useCallback((secs?: number) => {
    setIsRunning(false);
    setSeconds(secs ?? initialSeconds);
  }, [initialSeconds]);

  const formatted = `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, "0")}`;

  return { seconds, formatted, isRunning, start, pause, reset };
}
