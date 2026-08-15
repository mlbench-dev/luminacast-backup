import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import type { BackgroundConfig } from "@/lib/types";

const BG_OPTIONS = [
  { type: "original" as const, label: "Original", desc: "Keep avatar's scene" },
  { type: "blur-sm" as const, label: "Blurred", desc: "Blur original scene" },
  { type: "color" as const, label: "Color", desc: "Solid color" },
  { type: "gradient" as const, label: "Gradient", desc: "Two-color blend" },
  { type: "image" as const, label: "Image", desc: "Upload your own" },
  { type: "video" as const, label: "Video", desc: "Video loop" },
] as const;

interface BackgroundPickerProps {
  config: BackgroundConfig;
  onChange: (config: BackgroundConfig) => void;
  onUpload: (file: File, mediaType: "image" | "video") => void;
}

export function BackgroundPicker({ config, onChange, onUpload }: BackgroundPickerProps) {
  const bgType = config.type || "original";

  return (
    <div className="space-y-3">
      {/* Type selector */}
      <div className="grid grid-cols-3 gap-1.5">
        {BG_OPTIONS.map((opt) => (
          <button
            key={opt.type}
            onClick={() => onChange({ ...config, type: opt.type })}
            className={cn(
              "rounded-lg border p-1.5 text-center transition-all",
              bgType === opt.type
                ? "border-accent bg-accent/10"
                : "border-border hover:border-accent/50",
            )}
          >
            <p className="text-[10px] font-medium text-text">{opt.label}</p>
            <p className="text-[8px] text-text-muted">{opt.desc}</p>
          </button>
        ))}
      </div>

      {/* Type-specific options */}
      {bgType === "color" && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-text-dim">Color</span>
          <input
            type="color"
            value={config.color || "#1a1a2e"}
            onChange={(e) => onChange({ ...config, color: e.target.value })}
            className="h-7 w-full rounded cursor-pointer border border-border"
          />
        </div>
      )}

      {bgType === "gradient" && (
        <div className="space-y-2">
          <div className="flex gap-2">
            <div className="flex-1">
              <span className="text-[9px] text-text-muted block mb-0.5">From</span>
              <input
                type="color"
                value={config.gradient?.from || "#667eea"}
                onChange={(e) =>
                  onChange({ ...config, gradient: { ...config.gradient, from: e.target.value } })
                }
                className="h-7 w-full rounded cursor-pointer border border-border"
              />
            </div>
            <div className="flex-1">
              <span className="text-[9px] text-text-muted block mb-0.5">To</span>
              <input
                type="color"
                value={config.gradient?.to || "#764ba2"}
                onChange={(e) =>
                  onChange({ ...config, gradient: { ...config.gradient, to: e.target.value } })
                }
                className="h-7 w-full rounded cursor-pointer border border-border"
              />
            </div>
          </div>
          <select
            value={config.gradient?.direction || "vertical"}
            onChange={(e) =>
              onChange({
                ...config,
                gradient: { ...config.gradient, direction: e.target.value as "vertical" | "horizontal" | "diagonal" },
              })
            }
            className="w-full h-6 rounded border border-border bg-bg px-1.5 text-[10px] text-text"
          >
            <option value="vertical">Vertical</option>
            <option value="horizontal">Horizontal</option>
            <option value="diagonal">Diagonal</option>
          </select>
        </div>
      )}

      {bgType === "blur-sm" && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-text-dim">Blur strength</span>
            <span className="text-[10px] text-text-muted">{config.blur_strength || 20}</span>
          </div>
          <input
            type="range"
            min="5"
            max="50"
            value={config.blur_strength || 20}
            onChange={(e) => onChange({ ...config, blur_strength: parseInt(e.target.value) })}
            className="w-full h-1.5 accent-accent"
          />
        </div>
      )}

      {bgType === "image" && (
        <div className="space-y-2">
          <input
            type="file"
            accept="image/*"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onUpload(file, "image");
            }}
            className="text-[10px] text-text-dim w-full"
          />
          {config.image_key && (
            <img
              src={cdnUrl(config.image_key)}
              className="w-full h-16 object-cover rounded border border-border"
              alt="Scene"
            />
          )}
        </div>
      )}

      {bgType === "video" && (
        <div className="space-y-1">
          <input
            type="file"
            accept="video/mp4,video/webm"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onUpload(file, "video");
            }}
            className="text-[10px] text-text-dim w-full"
          />
          <p className="text-[8px] text-text-muted">Short loop recommended (5-15 sec)</p>
        </div>
      )}

      {/* Animation (for image/gradient/color) */}
      {["image", "gradient", "color"].includes(bgType) && (
        <div>
          <span className="text-[10px] text-text-dim block mb-1">Animation</span>
          <select
            value={config.animation || "none"}
            onChange={(e) => onChange({ ...config, animation: e.target.value as "none" | "ken_burns" | "slow_zoom" })}
            className="w-full h-6 rounded border border-border bg-bg px-1.5 text-[10px] text-text"
          >
            <option value="none">None (static)</option>
            <option value="ken_burns">Ken Burns (slow zoom + pan)</option>
            <option value="slow_zoom">Slow Zoom In</option>
          </select>
        </div>
      )}
    </div>
  );
}
