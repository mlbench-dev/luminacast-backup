/**
 * FrameSlot — single image picker slot for generated_video blocks.
 *
 * Used twice per block: one for the FIRST frame, one for the LAST frame.
 * Shows a thumbnail when set, with a small × to clear, and a click/drop
 * target when empty. Drag-and-drop is handled at this component level only
 * (not at the block-card level) because the parent block uses native drag
 * for reordering — accepting file drops there would conflict.
 */
import { useRef, useState } from "react";
import { Upload, X, Loader2, ImagePlus } from "lucide-react";
import { cn } from "@/lib/cn";

const PUBLIC_BASE = "https://media.luminacast.com";

export interface FrameSlotProps {
  label: string;
  slot: "first" | "last";
  blockId: string;
  /** R2 key currently set for this slot (or null/undefined when empty). */
  r2Key?: string | null;
  uploading: boolean;
  onUpload: (file: File) => void;
  onClear: () => void;
}

export function FrameSlot({ label, r2Key, uploading, onUpload, onClear }: FrameSlotProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dropHover, setDropHover] = useState(false);

  const previewUrl = r2Key ? `${PUBLIC_BASE}/${r2Key}` : null;

  const handlePickClick = () => inputRef.current?.click();
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onUpload(file);
    // Reset so picking the same file again still triggers onChange.
    e.target.value = "";
  };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropHover(false);
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith("image/")) onUpload(file);
  };

  return (
    <div className="space-y-1">
      <div className="text-[10px] text-white/50 font-medium">{label}</div>
      <div
        onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setDropHover(true); }}
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
        onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setDropHover(false); }}
        onDrop={handleDrop}
        className={cn(
          "relative aspect-video rounded border bg-black/30 overflow-hidden transition-colors",
          dropHover ? "border-accent" : "border-white/10",
          previewUrl ? "" : "cursor-pointer hover:border-white/30",
        )}
        onClick={previewUrl ? undefined : handlePickClick}
        role={previewUrl ? undefined : "button"}
      >
        {previewUrl ? (
          <>
            <img src={previewUrl} alt={label} className="w-full h-full object-cover" />
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onClear(); }}
              className="absolute top-1 right-1 p-1 rounded-full bg-black/60 hover:bg-red-500/80 text-white/80 hover:text-white"
              title="Remove frame"
              aria-label="Remove frame"
            >
              <X className="w-3 h-3" />
            </button>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); handlePickClick(); }}
              className="absolute bottom-1 right-1 p-1 rounded-full bg-black/60 hover:bg-accent/80 text-white/80 hover:text-white"
              title="Replace"
              aria-label="Replace frame"
            >
              <Upload className="w-3 h-3" />
            </button>
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-white/40">
            {uploading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <ImagePlus className="w-5 h-5" />
                <span className="text-[10px]">Click or drop</span>
              </>
            )}
          </div>
        )}
        {/* Always-on overlay while uploading on top of an existing preview */}
        {previewUrl && uploading && (
          <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-white" />
          </div>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={handleFileChange}
      />
    </div>
  );
}
