import { Lock } from "lucide-react";

/**
 * Non-blocking notice shown at the top of the Setup / Script tabs while a
 * render is running. Replaces the old full-screen overlay that trapped the
 * user on the editor: navigation stays free, but the tab's content is made
 * `inert` (visible, not editable) so a mid-render edit can't race the render
 * task's live reads of quality / duration / script / voice.
 */
export function RenderLockBanner({ onCancelRender }: { onCancelRender?: () => void }) {
  return (
    <div className="sticky top-0 z-40 flex items-center gap-3 border-b border-amber-500/25 bg-amber-500/10 px-4 py-2 text-sm text-amber-200 backdrop-blur-sm">
      <Lock className="h-4 w-4 shrink-0" />
      <span className="flex-1">
        A render is in progress — this tab is read-only until it finishes.
      </span>
      {onCancelRender && (
        <button
          type="button"
          onClick={onCancelRender}
          className="shrink-0 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-100 transition-colors hover:bg-amber-500/20"
        >
          Cancel render to edit
        </button>
      )}
    </div>
  );
}
