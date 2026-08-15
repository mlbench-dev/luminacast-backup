import { X } from "lucide-react";
import { Button } from "@/components/ui/button";

interface RewriteDiffModalProps {
  open: boolean;
  onClose: () => void;
  original: string;
  rewritten: string;
  onAccept: (text: string) => void;
}

export function RewriteDiffModal({ open, onClose, original, rewritten, onAccept }: RewriteDiffModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" data-testid="rewrite-diff-modal">
      <div className="relative bg-surface border border-border rounded-xl max-w-4xl w-full mx-4 max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h3 className="text-sm font-semibold text-text">Rewrite in My Voice</h3>
          <button onClick={onClose} className="text-text-muted hover:text-text">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content - side by side */}
        <div className="flex-1 overflow-auto p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Original */}
            <div>
              <h4 className="text-xs font-medium text-text-muted mb-2">Original</h4>
              <div className="rounded-lg border border-border bg-bg p-4 text-sm text-text whitespace-pre-wrap min-h-[200px]">
                {original}
              </div>
            </div>

            {/* Rewritten */}
            <div>
              <h4 className="text-xs font-medium text-accent mb-2">
                Rewritten · powered by your voice examples
              </h4>
              <div className="rounded-lg border-2 border-accent/30 bg-bg p-4 text-sm text-text whitespace-pre-wrap min-h-[200px]">
                {rewritten}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border">
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            className="bg-accent hover:bg-accent/90"
            onClick={() => { onAccept(rewritten); onClose(); }}
            data-testid="use-this-button"
          >
            Use This
          </Button>
        </div>
      </div>
    </div>
  );
}
