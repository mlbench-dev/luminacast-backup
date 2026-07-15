import { Radio, SkipForward, Pause, Play, SkipBack } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useStreamStore } from "@/stores/streamStore";
import { StreamStatus } from "@/lib/types";
import { formatDuration } from "@/lib/billing";

interface NowPlayingProps {
  onSkip: () => void;
  onPrevious: () => void;
  onPauseResume: () => void;
}

export function NowPlaying({ onSkip, onPrevious, onPauseResume }: NowPlayingProps) {
  const status = useStreamStore((s) => s.status);
  const uptimeSeconds = useStreamStore((s) => s.uptimeSeconds);
  const currentBlock = useStreamStore((s) => s.currentBlock);
  const viewers = useStreamStore((s) => s.viewers);

  const isLive = status === StreamStatus.LIVE;
  const isPaused = status === StreamStatus.PAUSED;

  return (
    <div
      className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3"
      data-testid="now-playing-bar"
    >
      {/* Left: Status + Block info */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          {isLive && <span className="live-indicator" />}
          <Badge variant={isLive ? "live" : isPaused ? "warning" : "secondary"}>
            {isLive ? "LIVE" : isPaused ? "PAUSED" : "IDLE"}
          </Badge>
        </div>

        {currentBlock && (
          <div className="flex flex-col">
            <span className="text-sm font-medium text-text">
              Block {currentBlock.position + 1}/{currentBlock.totalBlocks} — {currentBlock.type.replace("_", " ").toUpperCase()}
            </span>
            {currentBlock.productToPin && (
              <span className="text-xs text-accent">
                Product: {currentBlock.productToPin.name}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Center: Controls */}
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          onClick={onPrevious}
          data-testid="stream-previous"
          disabled={!isLive && !isPaused}
        >
          <SkipBack className="h-4 w-4" />
        </Button>
        <Button
          variant={isPaused ? "success" : "outline"}
          size="icon"
          onClick={onPauseResume}
          data-testid="stream-pause-resume"
          disabled={!isLive && !isPaused}
        >
          {isPaused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onSkip}
          data-testid="stream-skip"
          disabled={!isLive && !isPaused}
        >
          <SkipForward className="h-4 w-4" />
        </Button>
      </div>

      {/* Right: Stats */}
      <div className="flex items-center gap-4 text-sm text-text-dim">
        <div className="flex items-center gap-1">
          <Radio className="h-3 w-3" />
          <span data-testid="viewer-count">{viewers}</span>
        </div>
        <span data-testid="uptime">{formatDuration(uptimeSeconds)}</span>
      </div>
    </div>
  );
}
