import { cn } from "@/lib/cn";

/**
 * CrossfadePreview — stacks the start and end pinned frames and
 * crossfades between them in an infinite 2-second alternate loop. Used
 * by the action-block storyboard thumbnail so the user can see the
 * motion intent before rendering. CSS-only — no JS timer.
 *
 * Behaviour:
 *   - both URLs present → crossfade
 *   - only one URL → render that single image (no animation)
 *   - neither       → render nothing; caller falls back to placeholder
 */
interface Props {
  startUrl: string | null | undefined;
  endUrl: string | null | undefined;
  alt?: string;
  className?: string;
}

export function CrossfadePreview({ startUrl, endUrl, alt, className }: Props) {
  const hasStart = !!startUrl;
  const hasEnd = !!endUrl;
  if (!hasStart && !hasEnd) return null;

  // Single-frame fallback: if only one is pinned, show it static. The
  // caller decides whether to use this component at all, so this branch
  // is mostly defensive.
  if (!hasStart || !hasEnd) {
    const url = (startUrl || endUrl) as string;
    return (
      <img
        src={url}
        alt={alt ?? "Action frame"}
        className={cn("absolute inset-0 w-full h-full object-cover", className)}
        loading="lazy"
      />
    );
  }

  return (
    <>
      {/* Start frame — fully opaque underneath. */}
      <img
        src={startUrl as string}
        alt={alt ? `${alt} (start)` : "Start frame"}
        className={cn("absolute inset-0 w-full h-full object-cover", className)}
        loading="lazy"
      />
      {/* End frame — animates opacity 0↔1 over 2s alternate, ease-in-out.
          The keyframe is registered as --animate-crossfade-2s in
          globals.css, which Tailwind v4 exposes as the `animate-crossfade-2s`
          utility class. */}
      <img
        src={endUrl as string}
        alt={alt ? `${alt} (end)` : "End frame"}
        className={cn(
          "absolute inset-0 w-full h-full object-cover animate-crossfade-2s motion-reduce:animate-none motion-reduce:opacity-100",
          className,
        )}
        loading="lazy"
      />
    </>
  );
}
