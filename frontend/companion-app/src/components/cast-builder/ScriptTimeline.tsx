import { cn } from "@/lib/cn";
import { BlockType } from "@/lib/types";

interface TimelineBlock {
  id: string;
  position: number;
  type: BlockType;
  productName?: string;
}

interface ScriptTimelineProps {
  blocks: TimelineBlock[];
  currentPosition: number;
}

const blockColors: Partial<Record<BlockType, string>> = {
  [BlockType.INTRO]: "bg-info",
  [BlockType.PRODUCT]: "bg-accent",
  [BlockType.FLASH_SALE]: "bg-warning",
  [BlockType.SOCIAL_PROOF]: "bg-success",
  [BlockType.CTA]: "bg-danger",
  [BlockType.FILLER]: "bg-text-muted",
  [BlockType.IDLE]: "bg-border",
  [BlockType.CLOSING]: "bg-info",
};

export function ScriptTimeline({ blocks, currentPosition }: ScriptTimelineProps) {
  return (
    <div
      className="rounded-lg border border-border bg-surface p-4"
      data-testid="script-timeline"
    >
      <h3 className="mb-3 text-sm font-semibold text-text">Script Timeline</h3>
      <div className="flex gap-1">
        {blocks.map((block) => (
          <div
            key={block.id}
            className={cn(
              "group relative flex-1 cursor-pointer rounded-sm transition-all",
              blockColors[block.type],
              block.position === currentPosition
                ? "h-10 opacity-100 ring-2 ring-white/30"
                : block.position < currentPosition
                  ? "h-8 opacity-40"
                  : "h-8 opacity-60"
            )}
            data-testid={`timeline-block-${block.position}`}
          >
            {/* Tooltip */}
            <div className="pointer-events-none absolute -top-10 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded bg-bg px-2 py-1 text-xs text-text opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
              {block.type.replace("_", " ")}
              {block.productName && ` — ${block.productName}`}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-xs text-text-muted">
        <span>Start</span>
        <span>Block {currentPosition + 1} / {blocks.length}</span>
        <span>End</span>
      </div>
    </div>
  );
}
