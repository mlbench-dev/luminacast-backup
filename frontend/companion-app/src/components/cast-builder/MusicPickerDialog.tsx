import { useState, useEffect } from "react";
import { aceStepApi, type MusicSoundCast, type MusicTrackItem } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Loader2, Music, ArrowLeft, Play, Pause } from "lucide-react";

export interface MusicPickResult {
  track: MusicTrackItem;
  audio_url: string;
}

interface MusicPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (result: MusicPickResult) => void;
}

type Step = "soundcast" | "tracks";

export function MusicPickerDialog({
  open,
  onOpenChange,
  onPick,
}: MusicPickerDialogProps) {
  const [step, setStep] = useState<Step>("soundcast");
  const [soundCasts, setSoundCasts] = useState<MusicSoundCast[]>([]);
  const [tracks, setTracks] = useState<MusicTrackItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedSc, setSelectedSc] = useState<MusicSoundCast | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (open) {
      setStep("soundcast");
      setSelectedSc(null);
      setTracks([]);
      setLoading(true);
      aceStepApi
        .listSoundCasts()
        .then((scs) => setSoundCasts(scs))
        .catch(console.error)
        .finally(() => setLoading(false));
    } else {
      // Stop playback on close
      if (audioEl) {
        audioEl.pause();
        setAudioEl(null);
        setPlayingId(null);
      }
    }
  }, [open]);

  const handleScSelect = async (sc: MusicSoundCast) => {
    setSelectedSc(sc);
    setStep("tracks");
    setLoading(true);
    try {
      const t = await aceStepApi.listTracks(sc.id);
      setTracks(t);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const togglePreview = (track: MusicTrackItem) => {
    if (playingId === track.id) {
      audioEl?.pause();
      setPlayingId(null);
      return;
    }
    if (audioEl) audioEl.pause();
    const url = track.audio_url || cdnUrl(track.id);
    const el = new Audio(url);
    el.play().catch(console.error);
    el.onended = () => setPlayingId(null);
    setAudioEl(el);
    setPlayingId(track.id);
  };

  const handleTrackPick = (track: MusicTrackItem) => {
    if (audioEl) audioEl.pause();
    onPick({
      track,
      audio_url: track.audio_url || cdnUrl(track.id),
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[70vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2">
            {step === "tracks" && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { setStep("soundcast"); setSelectedSc(null); }}
                className="text-white/60 hover:text-white p-1"
              >
                <ArrowLeft className="w-4 h-4" />
              </Button>
            )}
            <DialogTitle>
              {step === "soundcast" ? "Select Sound Cast" : `Tracks — ${selectedSc?.name}`}
            </DialogTitle>
          </div>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-white/50" />
          </div>
        ) : step === "soundcast" ? (
          <div className="space-y-2">
            {soundCasts.length === 0 && (
              <div className="text-center text-white/40 py-8">
                <Music className="w-8 h-8 mx-auto mb-2 opacity-40" />
                No tracks yet
              </div>
            )}
            {soundCasts.map((sc) => (
              <button
                key={sc.id}
                onClick={() => handleScSelect(sc)}
                className="w-full flex items-center gap-3 p-3 rounded-lg border border-white/10 hover:border-purple-500/50 hover:bg-white/5 transition-colors text-left"
              >
                <Music className="w-5 h-5 text-purple-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white/90 truncate">{sc.name}</div>
                  <div className="text-xs text-white/40">
                    {sc.status} · {sc.training_audio_count} samples
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {tracks.length === 0 && (
              <div className="text-center text-white/40 py-8">
                <Music className="w-8 h-8 mx-auto mb-2 opacity-40" />
                No tracks yet
              </div>
            )}
            {tracks.map((track) => (
              <div
                key={track.id}
                className="flex items-center gap-3 p-3 rounded-lg border border-white/10 hover:border-purple-500/50 transition-colors"
              >
                <button
                  onClick={() => togglePreview(track)}
                  className="w-8 h-8 flex items-center justify-center rounded-full bg-white/10 hover:bg-white/20 shrink-0"
                >
                  {playingId === track.id ? (
                    <Pause className="w-4 h-4 text-white" />
                  ) : (
                    <Play className="w-4 h-4 text-white" />
                  )}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white/90 truncate">{track.name}</div>
                  <div className="text-xs text-white/40">
                    {track.duration_seconds ? `${Math.round(track.duration_seconds)}s` : "—"} · {track.status}
                  </div>
                </div>
                <Button
                  size="sm"
                  onClick={() => handleTrackPick(track)}
                  className="bg-purple-600 hover:bg-purple-700 text-white text-xs"
                >
                  Use
                </Button>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
