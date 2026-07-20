import { useRef, useCallback, useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, Trash2, Loader2, AlertCircle, Clock, FileAudio, Mic, Square, Play, Pause } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { voiceCorpusApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cdnUrl } from "@/lib/cdn";
import { confirmAction } from "@/lib/swal";
import type { VoiceCorpusEntry } from "@/lib/types";

interface VoiceCorpusTabProps {
  avatarId: string | null;
  ensureAvatarId: () => Promise<string>;
  compact?: boolean;
  onTrained?: (voiceId: string) => void;
}

// Minimum total ready speech (seconds) before training is allowed.
// Mirrors MIN_VOICE_TRAIN_SECONDS on the backend.
const MIN_TRAIN_SECONDS = 8;

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-amber-900/50 text-amber-400 border-amber-500/30",
  processing: "bg-blue-900/50 text-blue-400 border-blue-500/30",
  ready: "bg-green-900/50 text-green-400 border-green-500/30",
  failed: "bg-red-900/50 text-red-400 border-red-500/30",
};

function formatDuration(seconds: number | undefined): string {
  if (!seconds) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function VoiceCorpusTab({ avatarId, ensureAvatarId, compact, onTrained }: VoiceCorpusTabProps) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Per-entry selection override. Absent = selected by default, so newly
  // processed entries are included automatically; `false` = user deselected.
  const [deselected, setDeselected] = useState<Record<string, boolean>>({});
  const [uploadingCount, setUploadingCount] = useState(0);


  const { data, isLoading } = useQuery({
    queryKey: ["voice-corpus", avatarId],
    queryFn: () => voiceCorpusApi.list(avatarId!),
    enabled: !!avatarId,
    refetchInterval: (q) => {
      const entries: VoiceCorpusEntry[] = q.state.data?.entries || [];
      return entries.some((e) => e.status === "pending" || e.status === "processing") ? 5000 : false;
    },
  });

  const entries: VoiceCorpusEntry[] = data?.entries || [];
  const totalDuration = data?.total_ready_duration_seconds
    ?? entries.filter((e) => e.status === "ready").reduce((sum, e) => sum + (e.duration_seconds || 0), 0);
  const readyCount = entries.filter((e) => e.status === "ready").length;



  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const ensuredId = avatarId || await ensureAvatarId();
      return voiceCorpusApi.upload(ensuredId, file);
    },
    onMutate: () => {
      setUploadingCount((c) => c + 1);
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["voice-corpus", avatarId] });   // ← await this now
      toast({ title: "Processing voice example..." });
    },
    onError: (err: any) =>
      toast({ title: "Upload failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" }),
    onSettled: () => {
      setUploadingCount((c) => Math.max(0, c - 1));   // now fires only after the list has actually refreshed
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (entryId: string) => voiceCorpusApi.delete(avatarId!, entryId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["voice-corpus", avatarId] });
      toast({ title: "Voice example deleted" });
    },
    onError: () => toast({ title: "Delete failed", variant: "destructive" }),
  });

  const readyEntries = entries.filter((e) => e.status === "ready");
  const selectedEntries = readyEntries.filter((e) => deselected[e.id] !== true);
  const selectedDuration = selectedEntries.reduce((sum, e) => sum + (e.duration_seconds || 0), 0);
  const canTrain = selectedEntries.length > 0 && selectedDuration >= MIN_TRAIN_SECONDS;

  const trainMutation = useMutation({
    mutationFn: (ids: string[]) => voiceCorpusApi.trainFromCorpus(avatarId!, ids),
    onSuccess: (res: { voice_id: string }) => {
      qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
      qc.invalidateQueries({ queryKey: ["avatar-identity", avatarId] });
      toast({ title: "Voice trained", description: "Your custom voice is ready." });
      onTrained?.(res.voice_id);
    },
    onError: (err: any) =>
      toast({ title: "Voice training failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" }),
  });

  const toggleSelected = (id: string) =>
    setDeselected((prev) => ({ ...prev, [id]: !prev[id] }));

  const handleTrain = () => {
    if (!canTrain || trainMutation.isPending) return;
    trainMutation.mutate(selectedEntries.map((e) => e.id));
  };

  const trainPanel = readyEntries.length > 0 ? (
    <div className="space-y-1.5" data-testid="voice-train-panel">
      <Button
        onClick={handleTrain}
        disabled={!canTrain || trainMutation.isPending}
        className="w-full"
        data-testid="train-voice-btn"
      >
        {trainMutation.isPending ? (
          <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Training voice...</>
        ) : (
          <>Train voice ({selectedEntries.length} selected · {formatDuration(selectedDuration)})</>
        )}
      </Button>
      {!canTrain && (
        <p className="text-[10px] text-text-muted text-center">
          {selectedEntries.length === 0
            ? "Select at least one ready example to train."
            : `Need at least ${MIN_TRAIN_SECONDS}s of voice to train (selected ${formatDuration(selectedDuration)}).`}
        </p>
      )}
    </div>
  ) : null;

  const handleFiles = useCallback((files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((file) => {
      if (file.size > 200 * 1024 * 1024) {
        toast({ title: "File too large", description: "Max 200MB per file", variant: "destructive" });
        return;
      }
      uploadMutation.mutate(file);
    });
  }, [uploadMutation]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const [corpusTab, setCorpusTab] = useState<"record" | "upload">("upload");
  const [isRecording, setIsRecording] = useState(false);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedPreviewUrl, setRecordedPreviewUrl] = useState<string | null>(null);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingTimerRef = useRef<NodeJS.Timeout | null>(null);
  const audioPreviewRef = useRef<HTMLAudioElement | null>(null);
  const [audioLevel, setAudioLevel] = useState(0); // 0–1, current mic volume
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!recordedBlob) {
      setRecordedPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(recordedBlob);
    setRecordedPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [recordedBlob]);

  const monitorAudioLevel = useCallback((stream: MediaStream) => {
    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);

    audioContextRef.current = audioContext;
    analyserRef.current = analyser;

    const dataArray = new Uint8Array(analyser.frequencyBinCount);

    const tick = () => {
      analyser.getByteFrequencyData(dataArray);
      const avg = dataArray.reduce((sum, v) => sum + v, 0) / dataArray.length;
      setAudioLevel(Math.min(1, avg / 128)); // normalize roughly to 0–1
      rafRef.current = requestAnimationFrame(tick);
    };
    tick();
  }, []);

  const stopMonitoringAudioLevel = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    audioContextRef.current?.close().catch(() => { });
    audioContextRef.current = null;
    analyserRef.current = null;
    setAudioLevel(0);
  }, []);

  useEffect(() => {
    return () => stopMonitoringAudioLevel();
  }, [stopMonitoringAudioLevel]);

  // if (isLoading && !!avatarId) {
  //   return (
  //     <div className="flex items-center justify-center py-12">
  //       <Loader2 className="h-5 w-5 animate-spin text-accent" />
  //     </div>
  //   );
  // }



  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        setRecordedBlob(blob);
        stream.getTracks().forEach((t) => t.stop());
        stopMonitoringAudioLevel();
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
      setRecordingDuration(0);
      setRecordedBlob(null);
      recordingTimerRef.current = setInterval(() => setRecordingDuration((d) => d + 1), 1000);
      monitorAudioLevel(stream); // NEW
    } catch {
      toast({ title: "Microphone access denied", description: "Please allow microphone access to record.", variant: "destructive" });
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
    if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
  };

  const useRecording = () => {
    if (!recordedBlob) return;
    const file = new File([recordedBlob], "voice_recording.webm", { type: "audio/webm" });
    uploadMutation.mutate(file);   // unchanged — already routes through the lazy mutationFn above
    setRecordedBlob(null);
    setRecordingDuration(0);
  };





  if (compact) {
    return (
      <div className="space-y-2" data-testid="voice-corpus-tab-compact">
        {/* Compact tabs: Record / Upload */}
        <div className="flex gap-1 rounded-lg border border-border p-0.5 bg-bg">
          <button onClick={() => setCorpusTab("record")} className={cn("flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition", corpusTab === "record" ? "bg-accent text-white" : "text-text-muted hover:text-text")} data-testid="corpus-tab-record">
            <Mic className="h-3 w-3" /> Record
          </button>
          <button onClick={() => setCorpusTab("upload")} className={cn("flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition", corpusTab === "upload" ? "bg-accent text-white" : "text-text-muted hover:text-text")} data-testid="corpus-tab-upload">
            <Upload className="h-3 w-3" /> Upload
          </button>
        </div>

        {corpusTab === "record" ? (
          <div className="rounded-lg border border-border bg-surface p-4 text-center space-y-3">
            {!isRecording && !recordedBlob && (
              <button onClick={startRecording} className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-red-500 hover:bg-red-600 transition">
                <Mic className="h-6 w-6 text-white" />
              </button>
            )}
            {isRecording && (
              <>
                <button
                  onClick={stopRecording}
                  className="mx-auto flex items-center justify-center rounded-full bg-red-600 transition-transform duration-75"
                  style={{
                    height: `${64 + audioLevel * 32}px`,
                    width: `${64 + audioLevel * 32}px`,
                    boxShadow: `0 0 ${audioLevel * 40}px rgba(239, 68, 68, ${0.4 + audioLevel * 0.5})`,
                  }}
                >
                  <Square className="h-6 w-6 text-white" />
                </button>
                <p className="text-sm text-red-400 font-medium">{recordingDuration}s — Click to stop</p>
              </>
            )}
            {recordedBlob && !isRecording && (
              <div className="space-y-2">
                <audio ref={audioPreviewRef} src={recordedPreviewUrl ?? undefined} controls className="h-8 w-full" />
                <div className="flex gap-2 justify-center">
                  <Button size="sm" variant="outline" onClick={() => { setRecordedBlob(null); setRecordingDuration(0); }}>Discard</Button>
                  <Button size="sm" onClick={useRecording}>Use this</Button>
                </div>
              </div>
            )}
            {!isRecording && !recordedBlob && <p className="text-[10px] text-text-muted">Click to record your voice</p>}
          </div>
        ) : (
          <div
            className="rounded-lg border-2 border-dashed border-border hover:border-accent/50 transition-colors p-4 text-center cursor-pointer"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            {uploadingCount > 0 ? (
              <div className="flex justify-center items-center gap-2 text-xs text-text-muted py-1">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
                Uploading...
              </div>
            ) : (
              <>
                <Upload className="h-5 w-5 mx-auto text-text-muted mb-1" />
                <p className="text-xs text-text-muted">Drop audio/video files or click to upload</p>
                <p className="text-[10px] text-text-muted mt-1">Accepts .mp3, .wav, .m4a, .mp4, .mov, .webm</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="audio/*,video/*,.mp3,.wav,.m4a,.mp4,.mov,.webm,.mkv"
                  multiple
                  className="hidden"
                  onChange={(e) => handleFiles(e.target.files)}
                />
              </>
            )}

          </div>
        )}
        {/* Recorded/uploaded examples — select which to train on */}
        {entries.length > 0 && (
          <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1" data-testid="voice-corpus-list-compact">
            {[...entries].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).map((entry) => (
              <div key={entry.id} className="flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5" data-testid={`corpus-entry-${entry.id}`}>
                {entry.status === "ready" ? (
                  <input
                    type="checkbox"
                    checked={deselected[entry.id] !== true}
                    onChange={() => toggleSelected(entry.id)}
                    className="h-3.5 w-3.5 shrink-0 accent-[var(--accent-active)]"
                    data-testid={`corpus-select-${entry.id}`}
                    title="Use this example for training"
                  />
                ) : (
                  <span className="h-3.5 w-3.5 shrink-0" />
                )}
                <Badge className={`text-[9px] border ${STATUS_COLORS[entry.status] || STATUS_COLORS.pending}`}>
                  {entry.status}
                </Badge>
                {entry.status === "ready" && entry.duration_seconds ? (
                  <span className="text-[10px] text-text-muted shrink-0">{formatDuration(entry.duration_seconds)}</span>
                ) : null}
                {entry.status === "ready" && entry.audio_r2_key && (
                  <audio controls preload="none" src={cdnUrl(entry.audio_r2_key)} className="h-7 flex-1 min-w-0" data-testid={`audio-player-${entry.id}`} />
                )}
                {entry.status !== "ready" && (
                  <span className="text-[10px] italic text-text-muted flex-1">
                    {entry.status === "processing" ? "Processing..." : entry.status === "pending" ? "Waiting..." : (entry.error_message || "Failed")}
                  </span>
                )}
                <button
                  onClick={async () => {
                    if (await confirmAction({
                      title: "Delete this voice example?",
                      text: "This sample will be removed from the avatar's voice corpus.",
                      confirmButtonText: "Delete",
                    })) {
                      deleteMutation.mutate(entry.id);
                    }
                  }}
                  className="text-text-muted hover:text-red-400 shrink-0"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {trainPanel}
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="voice-corpus-tab">
      {/* Summary bar */}
      <div className="text-sm text-text-muted">
        {readyCount > 0
          ? `${readyCount} voice example${readyCount > 1 ? "s" : ""} · ${formatDuration(totalDuration)} total`
          : "No voice examples yet"}
      </div>

      {/* Tabs: Record / Upload */}
      <div className="flex gap-1 rounded-lg border border-border p-0.5 bg-bg">
        <button onClick={() => setCorpusTab("record")} className={cn("flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition", corpusTab === "record" ? "bg-accent text-white" : "text-text-muted hover:text-text")} data-testid="corpus-tab-record">
          <Mic className="h-3.5 w-3.5" /> Record
        </button>
        <button onClick={() => setCorpusTab("upload")} className={cn("flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition", corpusTab === "upload" ? "bg-accent text-white" : "text-text-muted hover:text-text")} data-testid="corpus-tab-upload">
          <Upload className="h-3.5 w-3.5" /> Upload
        </button>
      </div>

      {corpusTab === "record" ? (
        <div className="rounded-lg border border-border bg-surface p-6 text-center space-y-4">
          {!isRecording && !recordedBlob && (
            <>
              <button onClick={startRecording} className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-red-500 hover:bg-red-600 transition shadow-lg">
                <Mic className="h-7 w-7 text-white" />
              </button>
              <p className="text-xs text-text-muted">Click to start recording your voice</p>
            </>
          )}
          {isRecording && (
            <>
              <button
                onClick={stopRecording}
                className="mx-auto flex items-center justify-center rounded-full bg-red-600 transition-transform duration-75"
                style={{
                  height: `${64 + audioLevel * 32}px`,
                  width: `${64 + audioLevel * 32}px`,
                  boxShadow: `0 0 ${audioLevel * 40}px rgba(239, 68, 68, ${0.4 + audioLevel * 0.5})`,
                }}
              >
                <Square className="h-6 w-6 text-white" />
              </button>
              <p className="text-sm text-red-400 font-medium">{recordingDuration}s — Click to stop</p>
            </>
          )}
          {recordedBlob && !isRecording && (
            <div className="space-y-3">
              <p className="text-xs text-text-muted">Recording preview ({recordingDuration}s)</p>
              <audio ref={audioPreviewRef} src={recordedPreviewUrl ?? undefined} controls className="h-8 w-full max-w-xs mx-auto" />              <div className="flex gap-2 justify-center">
                <Button size="sm" variant="outline" onClick={() => { setRecordedBlob(null); setRecordingDuration(0); }}>Discard</Button>
                <Button size="sm" onClick={useRecording}>Use this recording</Button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div
          className="rounded-lg border-2 border-dashed border-border hover:border-accent/50 transition-colors p-8 text-center cursor-pointer"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          data-testid="voice-upload-dropzone"
        >
          <Upload className="h-8 w-8 mx-auto text-text-muted mb-3" />
          <p className="text-sm text-text-muted">Drop audio or video files here, or click to upload</p>
          <p className="text-xs text-text-muted mt-1">Accepts .mp3, .wav, .m4a, .mp4, .mov, .webm — we'll extract the audio and isolate your voice</p>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,video/*,.mp3,.wav,.m4a,.mp4,.mov,.webm,.mkv"
            multiple
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
            data-testid="voice-file-input"
          />
        </div>
      )}

      {/* Entry cards */}
      {entries.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border py-8 text-center">
          <FileAudio className="h-8 w-8 mx-auto text-text-muted mb-2" />
          <p className="text-sm text-text-muted">
            No voice examples yet. Upload videos of yourself talking to help your AI clone sound more like you.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {[...entries].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).map((entry) => (
            <div
              key={entry.id}
              className="rounded-lg border border-border bg-surface p-3"
              data-testid={`corpus-entry-${entry.id}`}
            >
              <div className="flex items-center gap-3">
                {/* Selection checkbox — choose which examples to train on */}
                {entry.status === "ready" ? (
                  <input
                    type="checkbox"
                    checked={deselected[entry.id] !== true}
                    onChange={() => toggleSelected(entry.id)}
                    className="h-4 w-4 shrink-0 accent-[var(--accent-active)]"
                    data-testid={`corpus-select-${entry.id}`}
                    title="Use this example for training"
                  />
                ) : (
                  <span className="h-4 w-4 shrink-0" />
                )}

                {/* Status badge */}
                <Badge className={`text-[10px] border ${STATUS_COLORS[entry.status] || STATUS_COLORS.pending}`}>
                  {entry.status}
                </Badge>

                {/* Duration */}
                {entry.status === "ready" && entry.duration_seconds && (
                  <span className="text-xs text-text-muted flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {formatDuration(entry.duration_seconds)}
                  </span>
                )}

                {/* Transcript preview */}
                <span className={`text-xs flex-1 truncate ${entry.status === "ready" ? "text-text" : "italic text-text-muted"}`}
                  data-testid={`transcript-${entry.id}`}>
                  {entry.status === "processing"
                    ? "Processing..."
                    : entry.status === "pending"
                      ? "Waiting to process..."
                      : entry.transcript
                        ? entry.transcript.slice(0, 120) + (entry.transcript.length > 120 ? "..." : "")
                        : ""}
                </span>

                {/* Audio player */}
                {entry.status === "ready" && entry.audio_r2_key && (
                  <audio
                    controls
                    preload="none"
                    src={cdnUrl(entry.audio_r2_key)}
                    className="h-8 w-40 shrink-0"
                    data-testid={`audio-player-${entry.id}`}
                  />
                )}

                {/* Delete button */}
                <button
                  onClick={async () => {
                    if (await confirmAction({
                      title: "Delete this voice example?",
                      text: "This sample will be removed from the avatar's voice corpus.",
                      confirmButtonText: "Delete",
                    })) {
                      deleteMutation.mutate(entry.id);
                    }
                  }}
                  className="text-text-muted hover:text-red-400 shrink-0"
                  title="Delete"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>

              {/* Error message for failed entries */}
              {entry.status === "failed" && entry.error_message && (
                <div className="mt-2 flex items-start gap-1.5 text-xs text-red-400">
                  <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  {entry.error_message}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {trainPanel}
    </div>
  );
}
