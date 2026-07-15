// src/components/cast-builder/hooks/useAnchoredPortal.ts
import { useEffect, useState, RefObject } from "react";

interface Position {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
}

/**
 * Computes fixed-position coordinates for a popover anchored to a trigger.
 * Re-computes on scroll, resize, and when `open` changes.
 * Places popover below trigger, right-aligned, with automatic flip if it would overflow viewport.
 */
export function useAnchoredPortal(
  triggerRef: RefObject<HTMLElement | null>,
  open: boolean,
  options: { align?: "left" | "right"; offset?: number; minWidth?: number } = {}
): Position | null {
  const { align = "right", offset = 4, minWidth = 320 } = options;
  const [pos, setPos] = useState<Position | null>(null);

  useEffect(() => {
    if (!open || !triggerRef.current) {
      setPos(null);
      return;
    }

    const compute = () => {
      if (!triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      const width = Math.max(minWidth, rect.width);
      let left = align === "right" ? rect.right - width : rect.left;
      // Clamp to viewport
      if (left < 8) left = 8;
      if (left + width > vw - 8) left = vw - width - 8;

      const top = rect.bottom + offset;
      const maxHeight = Math.min(500, vh - top - 16);

      setPos({ top, left, width, maxHeight });
    };

    compute();
    window.addEventListener("scroll", compute, true); // capture — catch all scroll containers
    window.addEventListener("resize", compute);
    return () => {
      window.removeEventListener("scroll", compute, true);
      window.removeEventListener("resize", compute);
    };
  }, [open, triggerRef, align, offset, minWidth]);

  return pos;
}
