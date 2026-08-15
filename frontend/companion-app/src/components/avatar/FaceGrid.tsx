import { Star } from "lucide-react";
import { cn } from "@/lib/cn";
import type { FaceCandidate } from "@/lib/types";

interface FaceGridProps {
  candidates: FaceCandidate[];
  selectedUrl: string | null;
  onSelect: (url: string) => void;
}

export function FaceGrid({ candidates, selectedUrl, onSelect }: FaceGridProps) {
  const sorted = [...candidates].sort((a, b) => b.score - a.score);

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4"
      data-testid="face-grid"
    >
      {sorted.map((c) => (
        <button
          key={c.url}
          onClick={() => onSelect(c.url)}
          className={cn(
            "group relative overflow-hidden rounded-lg border-2 transition-all",
            selectedUrl === c.url
              ? "border-accent shadow-lg shadow-accent/20"
              : "border-border hover:border-accent/50",
          )}
          data-testid={`face-candidate-${c.url}`}
        >
          <img
            src={c.url}
            alt={`Face candidate — score ${c.score}`}
            className="w-full object-cover"
            style={{ aspectRatio: "1/1" }}
          />
          {/* Score badge */}
          <div className="absolute bottom-1.5 right-1.5 flex items-center gap-0.5 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-white backdrop-blur-xs">
            <Star className="h-2.5 w-2.5 fill-warning text-warning" />
            {c.score.toFixed(1)}
          </div>
          {/* Selected indicator */}
          {selectedUrl === c.url && (
            <div className="absolute top-1.5 left-1.5 rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-medium text-white">
              Selected
            </div>
          )}
        </button>
      ))}
    </div>
  );
}
