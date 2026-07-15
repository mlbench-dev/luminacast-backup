import { cn } from "@/lib/cn";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PhotosSubfolderSelectorProps {
  activeSubfolder: "uploaded" | "generated";
  onSubfolderChange: (subfolder: "uploaded" | "generated") => void;
  onGenerateClick: () => void;
}

export function PhotosSubfolderSelector({
  activeSubfolder,
  onSubfolderChange,
  onGenerateClick,
}: PhotosSubfolderSelectorProps) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex gap-1 rounded-lg bg-surface-2 p-1">
        <button
          onClick={() => onSubfolderChange("uploaded")}
          className={cn(
            "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
            activeSubfolder === "uploaded"
              ? "bg-accent text-white shadow-xs"
              : "text-text-dim hover:text-text"
          )}
        >
          Uploaded
        </button>
        <button
          onClick={() => onSubfolderChange("generated")}
          className={cn(
            "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
            activeSubfolder === "generated"
              ? "bg-accent text-white shadow-xs"
              : "text-text-dim hover:text-text"
          )}
        >
          Generated
        </button>
      </div>
      <Button onClick={onGenerateClick} size="sm" variant="outline">
        <Sparkles className="mr-1.5 h-4 w-4" />
        Generate with AI
      </Button>
    </div>
  );
}
