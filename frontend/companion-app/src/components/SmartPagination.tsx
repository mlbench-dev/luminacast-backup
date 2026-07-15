import { Fragment, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

interface SmartPaginationProps {
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
}

export function SmartPagination({ page, totalPages, onPageChange }: SmartPaginationProps) {
  const [jumpInput, setJumpInput] = useState("");

  const pages = useMemo(() => {
    const set = new Set<number>();
    [1, 2, 3].forEach((p) => {
      if (p <= totalPages) set.add(p);
    });
    [page - 1, page, page + 1].forEach((p) => {
      if (p > 0 && p <= totalPages) set.add(p);
    });
    [totalPages - 2, totalPages - 1, totalPages].forEach((p) => {
      if (p > 0) set.add(p);
    });
    return [...set].sort((a, b) => a - b);
  }, [page, totalPages]);

  if (totalPages <= 1) return null;

  return (
    <div className="flex items-center justify-center gap-1.5 mt-4 flex-wrap">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page === 1}
        className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center text-white/40 hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed"
        aria-label="Previous page"
      >
        <ChevronLeft className="w-4 h-4" />
      </button>
      {pages.map((p, i) => (
        <Fragment key={p}>
          {i > 0 && p - pages[i - 1] > 1 && (
            <span className="text-white/20 text-xs px-1">…</span>
          )}
          <button
            onClick={() => onPageChange(p)}
            className={cn(
              "w-8 h-8 rounded-lg text-xs flex items-center justify-center transition-colors",
              p === page
                ? "bg-accent text-white font-medium"
                : "bg-white/5 text-white/40 hover:bg-white/10",
            )}
          >
            {p}
          </button>
        </Fragment>
      ))}
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page === totalPages}
        className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center text-white/40 hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed"
        aria-label="Next page"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
      <div className="ml-3 flex items-center gap-1.5">
        <input
          value={jumpInput}
          onChange={(e) => setJumpInput(e.target.value.replace(/\D/g, ""))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              const p = parseInt(jumpInput, 10);
              if (p >= 1 && p <= totalPages) onPageChange(p);
              setJumpInput("");
            }
          }}
          placeholder="Page"
          className="w-16 text-xs bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-center text-white/50 placeholder:text-white/30 focus:outline-none focus:border-accent/50"
        />
        <span className="text-[10px] text-white/20">of {totalPages}</span>
      </div>
    </div>
  );
}
