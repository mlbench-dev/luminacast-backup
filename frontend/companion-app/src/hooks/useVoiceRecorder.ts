import { useRef, useState, useCallback, useEffect } from "react";

interface UseVoiceRecorderReturn {
  isRecording: boolean;
  duration: number; // seconds elapsed
  audioFile: File | null;
  audioUrl: string | null;
  error: string | null;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  clearRecording: () => void;
}

export function useVoiceRecorder(maxDurationMs = 60_000): UseVoiceRecorderReturn {
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);

  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const clearRecording = useCallback(() => {
    setAudioFile(null);
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    setDuration(0);
    setError(null);
  }, [audioUrl]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    // Stream tracks cleanup
    if (mediaRecorderRef.current?.stream) {
      mediaRecorderRef.current.stream.getTracks().forEach((t) => t.stop());
    }
    setIsRecording(false);
  }, []);

  const startRecording = useCallback(async () => {
    setError(null);
    clearRecording();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // Pick best supported mime type
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/mp4")
        ? "audio/mp4"
        : "audio/webm";

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const ext = mimeType.includes("mp4") ? "mp4" : "webm";
        const file = new File([blob], `voice_${Date.now()}.${ext}`, { type: mimeType });
        setAudioFile(file);
        setAudioUrl(URL.createObjectURL(blob));
      };

      recorder.start(250); // collect data every 250ms
      setIsRecording(true);
      startTimeRef.current = Date.now();

      // Duration timer
      timerRef.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000);
        setDuration(elapsed);
        if (elapsed * 1000 >= maxDurationMs) {
          stopRecording();
        }
      }, 500);

    } catch (err: any) {
      const msg = err?.name === "NotAllowedError"
        ? "Microphone access denied. Please allow mic access in your browser settings."
        : err?.name === "NotFoundError"
        ? "No microphone found on this device."
        : `Microphone error: ${err?.message || "Unknown"}`;
      setError(msg);
    }
  }, [clearRecording, stopRecording, maxDurationMs]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
      if (mediaRecorderRef.current?.stream) {
        mediaRecorderRef.current.stream.getTracks().forEach((t) => t.stop());
      }
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return {
    isRecording, duration, audioFile, audioUrl, error,
    startRecording, stopRecording, clearRecording,
  };
}
