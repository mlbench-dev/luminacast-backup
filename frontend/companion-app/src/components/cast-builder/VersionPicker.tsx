/**
 * VersionPicker — Toolbar button showing save status + dropdown of all cast versions.
 *
 * Shows: statusDot + v{N} + relative time + History icon
 * Dropdown: portal to document.body to escape stacking contexts (Phase 1 fix)
 * Rich version rows with name, time, duration, block count, quality badge
 * Plus button for new version (always visible, outside dropdown)
 * Hover-group: tracks mouse in trigger OR portal content, 400ms close delay
 * Keyboard nav: Arrow keys cycle, Enter views, Escape closes
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { History, Loader2, Plus, RotateCcw, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { castsApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { useAnchoredPortal } from "./hooks/useAnchoredPortal";
import type { Cast } from "@/lib/types";

interface CastVersionItem {
  id: string;
  version: number;
  status_at_snapshot: string;
  change_summary?: string;
  name: string;
  block_count: number;
  duration_seconds: number | null;
  quality: string | null;
  render_status?: string | null;
  created_at: string;
}

interface VersionPickerProps {
  cast: Cast;
  onRestored: (updatedCast: Cast) => void;
  onForked?: (newVersion: number) => void;
  changeCount?: number;
}

type SaveStatus = "saving" | "saved" | "error";

// ── Helpers ──

function formatShortTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);

  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;

  const isToday = d.toDateString() === now.toDateString();
  if (isToday) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function QualityBadge({ quality }: { quality: string | null }) {
  if (!quality) return null;
  const label = quality === "hd_plus" ? "HD+" : quality === "hd" ? "HD" : "Simple";
  const color =
    quality === "hd_plus"
      ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
      : quality === "hd"
        ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
        : "bg-white/10 text-white/50 border-white/10";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${color}`}>
      {label}
    </span>
  );
}

// ── Component ──

export function VersionPicker({ cast, onRestored, onForked, changeCount = 0 }: VersionPickerProps) {
  const triggerRef = useRef<HTMLDivElement>(null);
  const portalRef = useRef<HTMLDivElement>(null);
  const namePortalRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<CastVersionItem[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved");
  const [viewingVersion, setViewingVersion] = useState<CastVersionItem | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [showNameInput, setShowNameInput] = useState(false);
  const [newVersionName, setNewVersionName] = useState("");
  const [justSavedVersionId, setJustSavedVersionId] = useState<string | null>(null);
  const [focusIdx, setFocusIdx] = useState<number>(-1);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Portal positioning
  const dropdownPos = useAnchoredPortal(triggerRef, open, { align: "right", minWidth: 340 });
  const nameInputPos = useAnchoredPortal(triggerRef, showNameInput, { align: "left", minWidth: 260 });

  // SINGLE hover-group manager — tracks mouse in trigger OR portal
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enter = () => {
    if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
    setOpen(true);
  };
  const leave = () => {
    hoverTimerRef.current = setTimeout(() => setOpen(false), 400);
  };

  // Fetch versions
  const fetchVersions = useCallback(async () => {
    if (!cast?.id) return;
    setVersionsLoading(true);
    try {
      const data = await castsApi.listVersions(cast.id);
      setVersions(data.versions || []);
    } catch {
      // silent
    } finally {
      setVersionsLoading(false);
    }
  }, [cast?.id]);

  useEffect(() => {
    fetchVersions();
  }, [fetchVersions, cast?.version]);

  // Track save status from timeline saves
  useEffect(() => {
    const handleSaving = () => setSaveStatus("saving");
    const handleSaved = () => setSaveStatus("saved");
    const handleError = () => setSaveStatus("error");
    window.addEventListener("luminacast:timeline-saving", handleSaving);
    window.addEventListener("luminacast:timeline-saved", handleSaved);
    window.addEventListener("luminacast:timeline-changed", handleError);
    return () => {
      window.removeEventListener("luminacast:timeline-saving", handleSaving);
      window.removeEventListener("luminacast:timeline-saved", handleSaved);
      window.removeEventListener("luminacast:timeline-changed", handleError);
    };
  }, []);

  // Click-outside close — check both trigger AND portal content
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (portalRef.current?.contains(t)) return;
      if (namePortalRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Escape close + keyboard nav
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); return; }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(versions.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter" && focusIdx >= 0 && focusIdx < versions.length) {
        e.preventDefault();
        handleViewVersion(versions[focusIdx]);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, versions, focusIdx]);

  // Reset focus index when dropdown closes
  useEffect(() => {
    if (!open) setFocusIdx(-1);
  }, [open]);

  // Focus name input when shown
  useEffect(() => {
    if (showNameInput && nameInputRef.current) {
      nameInputRef.current.focus();
    }
  }, [showNameInput]);

  const generateVersionName = useCallback(() => {
    const curV = cast.version ?? 1;
    const suffix = changeCount > 20 ? "overhaul" :
                   changeCount > 10 ? "refined" :
                   changeCount > 5 ? "polished" : "tweaked";
    return `v${curV} ${suffix}`;
  }, [cast.version, changeCount]);

  // After fork — keep dropdown open longer, highlight the new version
  const handleFork = useCallback(async (name: string) => {
    try {
      const result = await castsApi.fork(cast.id, name);
      await fetchVersions();
      toast({ title: `Saved v${result.new_version}`, description: name || undefined });
      setShowNameInput(false);
      setNewVersionName("");
      setOpen(true); // force open
      // Mark the newest version for animation
      setJustSavedVersionId(result.version_id || null);
      setTimeout(() => setJustSavedVersionId(null), 3000);
      // Notify parent with the new version number so it can update cast state
      onForked?.(result.new_version);
    } catch (err: any) {
      toast({ title: "Failed to save version", description: String(err?.response?.data?.detail || err?.message || ""), variant: "destructive" });
    }
  }, [cast?.id, fetchVersions, onForked]);

  const handleRestore = useCallback(async (versionId: string) => {
    if (!cast) return;
    setRestoring(true);
    try {
      const result = await castsApi.restoreVersion(cast.id, versionId);
      toast({ title: `Restored to v${result.from_version}`, description: `Now at v${result.new_version}` });
      const refreshed = await castsApi.get(cast.id);
      onRestored(refreshed);
      setViewingVersion(null);
      setOpen(false);
    } catch (err: any) {
      toast({ title: "Restore failed", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    } finally {
      setRestoring(false);
    }
  }, [cast, onRestored]);

  const handleViewVersion = useCallback((v: CastVersionItem) => {
    setViewingVersion(v);
    setOpen(false);
  }, []);

  const handleBackToCurrent = useCallback(() => {
    setViewingVersion(null);
  }, []);

  // Status dot
  const statusDot =
    saveStatus === "saving" ? (
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-400" />
      </span>
    ) : saveStatus === "error" ? (
      <span className="inline-flex rounded-full h-2 w-2 bg-red-400" />
    ) : (
      <span className="inline-flex rounded-full h-2 w-2 bg-green-400" />
    );

  const lastSavedVersion = versions[0];
  const relativeTime = lastSavedVersion ? formatShortTime(lastSavedVersion.created_at) : "";

  // Dropdown JSX — portaled to document.body
  const dropdown = dropdownPos && open && createPortal(
    <div
      ref={portalRef}
      className="border border-white/10 rounded-xl shadow-2xl py-1 overflow-y-auto animate-in fade-in slide-in-from-top-2 duration-150"
      style={{
        position: "fixed",
        top: dropdownPos.top,
        left: dropdownPos.left,
        width: dropdownPos.width,
        maxHeight: dropdownPos.maxHeight,
        backgroundColor: "#0d0d0d",
        zIndex: 10000,
      }}
      onMouseEnter={enter}
      onMouseLeave={leave}
    >
      <div className="px-3 py-2 text-[11px] uppercase tracking-wider text-white/40 border-b border-white/10 sticky top-0" style={{ backgroundColor: '#0d0d0d' }}>
        Versions
      </div>



      {/* Loading skeleton */}
      {versionsLoading && versions.length === 0 ? (
        <div className="px-4 py-3 flex flex-col gap-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex items-center gap-3 animate-pulse">
              <div className="w-16 h-3 bg-white/10 rounded" />
              <div className="w-24 h-3 bg-white/5 rounded" />
            </div>
          ))}
        </div>
      ) : versions.length === 0 ? (
        <div className="px-4 py-4 text-xs text-white/30">No saved snapshots yet. Hit + to save one.</div>
      ) : (
        versions.map((v, idx) => {
          const isLatest = idx === 0;
          const isRendered = v.render_status === "ready";
          const isRendering = v.render_status === "baking" || v.render_status === "composing" || v.render_status === "queued";
          return (
          <button
            key={v.id}
            onClick={() => handleViewVersion(v)}
            className={`w-full text-left px-4 py-3 cursor-pointer flex flex-col gap-1 border-b border-white/5 last:border-0 transition-all
              hover:bg-white/10 hover:border-l-2 hover:border-l-white/30 hover:pl-[14px]
              ${isLatest ? "bg-white/[0.04] border-l-2 border-l-accent pl-[14px]" : ""}
              ${justSavedVersionId === v.id ? "bg-green-500/10 border-l-2 border-l-green-500 pl-[14px] animate-pulse" : ""}
              ${focusIdx === idx ? "ring-1 ring-accent bg-white/10" : ""}
            `}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 min-w-0">
                {v.name
                  ? <span className="font-medium text-sm text-white/90 truncate">{v.name}</span>
                  : <span className="font-medium text-sm text-white/50">v{v.version}</span>
                }
                {isLatest && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/20 text-accent border border-accent/30 shrink-0">Current</span>
                )}
                {isRendered && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-300 border border-green-500/30 shrink-0">Rendered</span>
                )}
                {isRendering && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 shrink-0">Rendering</span>
                )}
                {justSavedVersionId === v.id && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-300 shrink-0">New</span>
                )}
              </div>
              <span className="text-[11px] text-white/40 shrink-0 ml-2">{formatShortTime(v.created_at)}</span>
            </div>
            <div className="flex items-center gap-3 text-[11px] text-white/40">
              <span>v{v.version}</span>
              <QualityBadge quality={v.quality} />
              <span>{v.block_count} block{v.block_count !== 1 ? "s" : ""}</span>
              {v.duration_seconds != null && v.duration_seconds > 0 && (
                <span>{formatDuration(v.duration_seconds)}</span>
              )}
            </div>
          </button>
          );
        })
      )}
    </div>,
    document.body
  );

  // Name input — ALSO portaled
  const nameInputPortal = nameInputPos && showNameInput && createPortal(
    <div
      ref={namePortalRef}
      className="border border-white/10 rounded-lg p-2 flex items-center gap-2"
      style={{
        position: "fixed",
        top: nameInputPos.top,
        left: nameInputPos.left,
        minWidth: nameInputPos.width,
        backgroundColor: "#0d0d0d",
        zIndex: 10001, // above dropdown
      }}
      onMouseEnter={enter}
      onMouseLeave={leave}
    >
      <input
        ref={nameInputRef}
        type="text"
        placeholder="Version name (optional)"
        value={newVersionName}
        onChange={e => setNewVersionName(e.target.value)}
        onKeyDown={e => {
          if (e.key === "Enter") handleFork(newVersionName);
          if (e.key === "Escape") { setShowNameInput(false); setNewVersionName(""); }
        }}
        className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-white placeholder:text-white/30 outline-none focus:border-white/20 w-48"
      />
      <button
        onClick={() => handleFork(newVersionName)}
        className="text-xs text-accent hover:text-accent/80 font-medium whitespace-nowrap"
      >
        Save
      </button>
      <button
        onClick={() => { setShowNameInput(false); setNewVersionName(""); }}
        className="text-xs text-white/40 hover:text-white/60"
      >
        Cancel
      </button>
    </div>,
    document.body
  );

  return (
    <div ref={triggerRef} className="relative flex items-center gap-0" onMouseEnter={enter} onMouseLeave={leave}>
      {/* Read-only banner when viewing old version */}
      {viewingVersion && (
        <div className="absolute -bottom-10 left-0 right-0 z-50 flex items-center gap-2 bg-amber-500/15 border border-amber-500/30 rounded-md px-3 py-1.5 text-xs text-amber-200 whitespace-nowrap">
          <span>Viewing v{viewingVersion.version}{viewingVersion.name ? ` "${viewingVersion.name}"` : ""} (read-only)</span>
          <button
            onClick={() => handleRestore(viewingVersion.id)}
            disabled={restoring}
            className="inline-flex items-center gap-1 text-amber-300 hover:text-amber-100 underline underline-offset-2 disabled:opacity-50"
          >
            <RotateCcw className="w-3 h-3" />
            {restoring ? "Restoring..." : "Restore"}
          </button>
          <button
            onClick={handleBackToCurrent}
            className="inline-flex items-center gap-1 text-amber-300 hover:text-amber-100 underline underline-offset-2"
          >
            <ArrowLeft className="w-3 h-3" />
            Back to current
          </button>
        </div>
      )}

      {/* Version indicator + save button in one frame */}
      <div className="flex items-center border border-white/10 rounded-md bg-white/5 hover:bg-white/10 transition-colors">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpen(v => !v)}
          className={`gap-1.5 text-xs h-8 rounded-r-none border-0 transition-colors ${open ? 'text-accent' : 'text-white/60 hover:text-white'}`}
          data-testid="version-picker-btn"
        >
          {statusDot}
          <span className="font-medium">v{cast.version ?? 1}</span>
          {relativeTime && <span className="text-white/40 hidden sm:inline">{relativeTime}</span>}
          <History className="w-3.5 h-3.5" />
        </Button>
        <div className="w-px h-4 bg-white/10" />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { setNewVersionName(generateVersionName()); setShowNameInput(v => !v); }}
          className="text-white/40 hover:text-accent h-8 w-8 p-0 rounded-l-none border-0"
          title="Save new version"
        >
          <Plus className="w-3.5 h-3.5" />
        </Button>
      </div>

      {dropdown}
      {nameInputPortal}
    </div>
  );
}
