import { useState, useRef, useEffect, useCallback } from "react";
import { Camera, Square, Upload, RefreshCw, Play, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { avatarApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";

const MIN_DURATION = 15;
const MAX_DURATION = 180;

function formatTime(secs: number) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface RecordCameraProps {
  onRecorded: (data: { video_r2_key: string; video_url: string; duration_seconds: number }) => void;
}

export function RecordCamera({ onRecorded }: RecordCameraProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reviewVideoRef = useRef<HTMLVideoElement>(null);

  const [stream, setStream] = useState<MediaStream | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [permissionDenied, setPermissionDenied] = useState(false);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [stream]);

  // Set review video src
  useEffect(() => {
    if (recordedBlob && reviewVideoRef.current) {
      reviewVideoRef.current.src = URL.createObjectURL(recordedBlob);
    }
  }, [recordedBlob]);

  const startCamera = useCallback(async () => {
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: 720, height: 1280 },
        audio: true,
      });
      setStream(s);
      setPermissionDenied(false);
      if (videoRef.current) videoRef.current.srcObject = s;
    } catch {
      setPermissionDenied(true);
      toast({ title: "Camera access denied", description: "Please allow camera and microphone permissions in your browser settings.", variant: "destructive" });
    }
  }, []);

  const startRecording = useCallback(() => {
    if (!stream) return;
    const chunks: Blob[] = [];
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: "video/webm" });
      setRecordedBlob(blob);
      stream.getTracks().forEach((t) => t.stop());
      setStream(null);
    };
    recorderRef.current = recorder;
    recorder.start();
    setIsRecording(true);
    setElapsed(0);

    timerRef.current = setInterval(() => {
      setElapsed((t) => {
        const next = t + 1;
        if (next >= MAX_DURATION) {
          recorder.stop();
          setIsRecording(false);
          if (timerRef.current) clearInterval(timerRef.current);
          toast({ title: "Maximum duration reached", description: "Recording stopped at 3 minutes." });
        }
        return next;
      });
    }, 1000);
  }, [stream]);

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    setIsRecording(false);
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  const discardRecording = useCallback(() => {
    setRecordedBlob(null);
    setElapsed(0);
  }, []);

  const uploadRecording = useCallback(async () => {
    if (!recordedBlob) return;
    setIsUploading(true);
    setUploadProgress(10);
    try {
      const file = new File([recordedBlob], "recording.webm", { type: "video/webm" });
      const interval = setInterval(() => setUploadProgress((p) => Math.min(p + 5, 90)), 500);
      const data = await avatarApi.uploadVideo(file);
      clearInterval(interval);
      setUploadProgress(100);
      onRecorded(data);
    } catch (err: any) {
      toast({ title: "Upload failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
    } finally {
      setIsUploading(false);
      setUploadProgress(0);
    }
  }, [recordedBlob, onRecorded]);

  return (
    <div className="space-y-4" data-testid="record-camera">
      {/* Camera preview / review */}
      <div className="relative mx-auto overflow-hidden rounded-lg bg-black" style={{ maxWidth: 320, aspectRatio: "9/16" }}>
        {recordedBlob ? (
          <video ref={reviewVideoRef} controls playsInline className="h-full w-full object-cover" />
        ) : (
          <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" style={{ transform: "scaleX(-1)" }} />
        )}

        {/* Recording indicator */}
        {isRecording && (
          <div className="absolute top-3 left-3 flex items-center gap-2 rounded-full bg-red-600 px-3 py-1 text-xs text-white">
            <span className="h-2 w-2 rounded-full bg-white animate-pulse" />
            {formatTime(elapsed)} / {formatTime(MAX_DURATION)}
          </div>
        )}
      </div>

      {/* Controls */}
      <div className="flex items-center justify-center gap-3">
        {!stream && !recordedBlob && (
          <Button onClick={startCamera} data-testid="start-camera-btn">
            <Camera className="mr-2 h-4 w-4" /> Start Camera
          </Button>
        )}

        {stream && !isRecording && !recordedBlob && (
          <Button variant="destructive" onClick={startRecording} data-testid="start-recording-btn">
            <span className="mr-2 h-3 w-3 rounded-full bg-white" /> Start Recording
          </Button>
        )}

        {isRecording && (
          <Button
            variant="outline"
            disabled={elapsed < MIN_DURATION}
            onClick={stopRecording}
            data-testid="stop-recording-btn"
          >
            <Square className="mr-2 h-4 w-4" />
            {elapsed < MIN_DURATION ? `Min ${MIN_DURATION}s (${MIN_DURATION - elapsed}s)` : "Stop Recording"}
          </Button>
        )}

        {recordedBlob && !isUploading && (
          <>
            <Button variant="outline" onClick={discardRecording}>
              <RefreshCw className="mr-2 h-4 w-4" /> Record again
            </Button>
            <Button onClick={uploadRecording} data-testid="use-recording-btn">
              <Upload className="mr-2 h-4 w-4" /> Use this recording
            </Button>
          </>
        )}
      </div>

      {/* Upload progress */}
      {isUploading && (
        <div className="space-y-2">
          <Progress value={uploadProgress} className="h-2" />
          <p className="text-xs text-text-muted text-center">Uploading recording... {uploadProgress}%</p>
        </div>
      )}

      {/* Permission denied message */}
      {permissionDenied && (
        <div className="rounded-lg bg-danger/10 border border-danger/30 p-3 text-center">
          <p className="text-xs text-danger">Camera access was denied.</p>
          <p className="text-[10px] text-text-muted mt-1">
            Click the camera icon in your browser's address bar to allow access, then try again.
          </p>
        </div>
      )}

      {/* Tips */}
      <div className="rounded-lg bg-surface/50 border border-border p-3 space-y-1">
        <p className="text-xs font-medium text-text-dim">Tips for best results</p>
        <ul className="space-y-0.5 text-[10px] text-text-muted">
          <li>• Face the camera directly with good lighting</li>
          <li>• Speak naturally for 30-60 seconds</li>
          <li>• Avoid background music</li>
          <li>• Indoor setting with minimal echo</li>
        </ul>
      </div>
    </div>
  );
}
