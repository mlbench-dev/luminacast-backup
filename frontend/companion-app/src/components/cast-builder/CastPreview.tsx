import { Play, RotateCcw, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";

interface CastPreviewProps {
  variantId: string;
  blockType: string;
  productName?: string;
  scriptPreview?: string;
  status: "pending" | "generating" | "ready" | "failed";
  onApprove?: () => void;
  onReject?: () => void;
  onRegenerate?: () => void;
}

export function CastPreview({
  variantId,
  blockType,
  productName,
  scriptPreview,
  status,
  onApprove,
  onReject,
  onRegenerate,
}: CastPreviewProps) {
  return (
    <div
      className="rounded-lg border border-border bg-card p-4"
      data-testid={`cast-preview-${variantId}`}
    >
      {/* Video placeholder */}
      <div className="relative mb-3 flex aspect-9/16 max-h-48 items-center justify-center rounded-md bg-bg">
        {status === "ready" ? (
          <Play className="h-8 w-8 text-accent" />
        ) : status === "generating" ? (
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        ) : status === "failed" ? (
          <X className="h-8 w-8 text-danger" />
        ) : (
          <div className="h-6 w-6 rounded-full bg-border" />
        )}

        <Badge
          variant={
            status === "ready" ? "success" : status === "failed" ? "danger" : "secondary"
          }
          className="absolute right-2 top-2 text-[10px]"
        >
          {status.toUpperCase()}
        </Badge>
      </div>

      {/* Info */}
      <div className="mb-2">
        <div className="text-xs font-medium text-accent">{blockType.replace("_", " ").toUpperCase()}</div>
        {productName && <div className="text-xs text-text-dim">{productName}</div>}
      </div>

      {scriptPreview && (
        <p className="mb-3 line-clamp-3 text-xs text-text-muted">{scriptPreview}</p>
      )}

      {/* Actions */}
      {status === "ready" && (
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="success"
            onClick={onApprove}
            data-testid={`approve-variant-${variantId}`}
            className="h-7 flex-1 text-xs"
          >
            <Check className="mr-1 h-3 w-3" />
            Approve
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onReject}
            data-testid={`reject-variant-${variantId}`}
            className="h-7 text-xs text-danger"
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      )}

      {status === "failed" && (
        <Button
          size="sm"
          variant="warning"
          onClick={onRegenerate}
          data-testid={`regen-variant-${variantId}`}
          className="h-7 w-full text-xs"
        >
          <RotateCcw className="mr-1 h-3 w-3" />
          Regenerate ($0.99)
        </Button>
      )}
    </div>
  );
}
