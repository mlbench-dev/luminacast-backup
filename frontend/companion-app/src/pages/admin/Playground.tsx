// Model Playground (admin) — fan one prompt / image out to every image &
// video model the app can call, side by side. Bench only; no cast/usage impact.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/apiClient";

type ModelKind = "image" | "video" | "talking_head";
type ModelInput = "text" | "image" | "text+image" | "image+audio";

interface PModel {
  id: string;
  label: string;
  provider: string;
  kind: ModelKind;
  input: ModelInput;
  image_optional: boolean;
  endpoint: string;
  est_cost_usd: number;
  note: string;
}

type RunState = "idle" | "queued" | "running" | "done" | "error";

interface RunResult {
  state: RunState;
  url?: string;
  error?: string;
  startedAt?: number;
  elapsed?: number;
  cost?: number;
}

const POLL_MS = 3000;
// Stop polling a card after this long so one slow model (Hallo…) can't lock
// the Run button forever. The fal job keeps running server-side.
const MAX_POLL_S = 1200;

export function PlaygroundPage() {
  const [models, setModels] = useState<PModel[]>([]);
  const [prompt, setPrompt] = useState("");
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [uploading, setUploading] = useState<"" | "image" | "audio">("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<Record<string, RunResult>>({});
  const [running, setRunning] = useState(false);
  const pollers = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get("/playground/models").then((r) => setModels(r.data.models || []));
    return () => {
      Object.values(pollers.current).forEach(clearInterval);
    };
  }, []);

  const imageModels = models.filter((m) => m.kind === "image");
  const videoModels = models.filter((m) => m.kind === "video");
  const talkingHeadModels = models.filter((m) => m.kind === "talking_head");

  const needsImage = (m: PModel) =>
    m.input === "image" || m.input === "image+audio" ||
    (m.input === "text+image" && !m.image_optional);
  const needsAudio = (m: PModel) => m.input === "image+audio";
  const blocked = (m: PModel) =>
    (needsImage(m) && !imageUrl) || (needsAudio(m) && !audioUrl);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const selectAll = (kind: ModelKind) =>
    setSelected((s) => {
      const n = new Set(s);
      models.filter((m) => m.kind === kind).forEach((m) => {
        if (blocked(m)) return;
        n.add(m.id);
      });
      return n;
    });

  const totalCost = useMemo(
    () =>
      models
        .filter((m) => selected.has(m.id))
        .reduce((a, m) => a + m.est_cost_usd, 0),
    [models, selected],
  );

  const onUpload = useCallback(async (f: File, kind: "image" | "audio") => {
    setUploading(kind);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await api.post(`/playground/upload?kind=${kind}`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      (kind === "image" ? setImageUrl : setAudioUrl)(r.data.url);
    } catch (e: any) {
      alert("Upload failed: " + (e?.response?.data?.detail || e.message));
    } finally {
      setUploading("");
    }
  }, []);

  const pollOne = useCallback((id: string, status_url: string, response_url: string, startedAt: number) => {
    const tick = async () => {
      try {
        if ((Date.now() - startedAt) / 1000 > MAX_POLL_S) {
          clearInterval(pollers.current[id]);
          delete pollers.current[id];
          setResults((prev) => ({
            ...prev,
            [id]: {
              ...(prev[id] || {}),
              state: "error",
              error: `Stopped polling after ${Math.round(MAX_POLL_S / 60)} min — the job may still finish on fal; check there.`,
              elapsed: MAX_POLL_S,
            },
          }));
          return;
        }
        const r = await api.post("/playground/poll", { status_url, response_url });
        const d = r.data;
        setResults((prev) => {
          const cur = prev[id] || {};
          const elapsed = cur.startedAt ? (Date.now() - cur.startedAt) / 1000 : cur.elapsed;
          if (d.state === "done" || d.state === "error") {
            clearInterval(pollers.current[id]);
            delete pollers.current[id];
          }
          return {
            ...prev,
            [id]: {
              ...cur,
              state: d.state,
              url: d.url,
              error: d.error,
              elapsed,
            },
          };
        });
      } catch (e: any) {
        setResults((prev) => ({
          ...prev,
          [id]: { ...(prev[id] || {}), state: "error", error: e?.response?.data?.detail || e.message },
        }));
        clearInterval(pollers.current[id]);
        delete pollers.current[id];
      }
    };
    pollers.current[id] = setInterval(tick, POLL_MS);
    tick();
  }, []);

  const run = useCallback(async () => {
    const chosen = models.filter((m) => selected.has(m.id) && !blocked(m));
    if (!chosen.length) return;
    setRunning(true);
    // clear old pollers/results for the chosen set
    chosen.forEach((m) => {
      if (pollers.current[m.id]) {
        clearInterval(pollers.current[m.id]);
        delete pollers.current[m.id];
      }
    });
    const startedAt = Date.now();
    setResults((prev) => {
      const n = { ...prev };
      chosen.forEach((m) => {
        n[m.id] = { state: "queued", startedAt, cost: m.est_cost_usd };
      });
      return n;
    });

    await Promise.all(
      chosen.map(async (m) => {
        try {
          const r = await api.post("/playground/run", {
            model_id: m.id,
            prompt: prompt.trim() || undefined,
            image_url: imageUrl || undefined,
            audio_url: audioUrl || undefined,
          });
          pollOne(m.id, r.data.status_url, r.data.response_url, startedAt);
        } catch (e: any) {
          setResults((prev) => ({
            ...prev,
            [m.id]: { state: "error", error: e?.response?.data?.detail || e.message, cost: m.est_cost_usd },
          }));
        }
      }),
    );
    setRunning(false);
  }, [models, selected, prompt, imageUrl, audioUrl, pollOne]);

  const anyPending = Object.values(results).some(
    (r) => r.state === "queued" || r.state === "running",
  );

  return (
    <div className="max-w-6xl mx-auto px-6 py-6 space-y-5 text-white">
      <header>
        <h1 className="text-xl font-semibold">Model Playground</h1>
        <p className="text-xs text-white/40 mt-0.5">
          One prompt / image → every image &amp; video model we can call, side by side.
          Charges fal per run. Admin only.
        </p>
      </header>

      {/* Inputs */}
      <div className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder="Prompt — e.g. 'cinematic product promo, slow orbit around the PC, RGB glow, dark studio'"
          className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm placeholder:text-white/25 focus:outline-none focus:border-accent/40 resize-none"
        />
        <div className="flex items-center gap-3 flex-wrap">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0], "image")}
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={!!uploading}
            className="text-xs px-3 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] disabled:opacity-40"
          >
            {uploading === "image" ? "Uploading…" : imageUrl ? "Replace image" : "Add source image"}
          </button>
          {imageUrl && (
            <div className="flex items-center gap-2">
              <img src={imageUrl} alt="" className="h-12 w-12 rounded object-cover border border-white/10" />
              <button onClick={() => setImageUrl(null)} className="text-[11px] text-white/40 hover:text-white/70">
                remove
              </button>
            </div>
          )}

          <input
            ref={audioRef}
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0], "audio")}
          />
          <button
            onClick={() => audioRef.current?.click()}
            disabled={!!uploading}
            className="text-xs px-3 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] disabled:opacity-40"
          >
            {uploading === "audio" ? "Uploading…" : audioUrl ? "Replace audio" : "Add audio (for talking head)"}
          </button>
          {audioUrl && (
            <div className="flex items-center gap-2">
              <audio src={audioUrl} controls className="h-8 w-40" />
              <button onClick={() => setAudioUrl(null)} className="text-[11px] text-white/40 hover:text-white/70">
                remove
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Model pickers */}
      {[
        { kind: "image" as const, label: "Image models", list: imageModels },
        { kind: "video" as const, label: "Video models", list: videoModels },
        { kind: "talking_head" as const, label: "Talking head (image + audio → lip-sync)", list: talkingHeadModels },
      ].map(({ kind, label, list }) => (
        <div key={kind} className="space-y-2">
          <div className="flex items-center gap-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-white/50">{label}</h2>
            <button onClick={() => selectAll(kind)} className="text-[11px] text-accent/80 hover:text-accent">
              select all
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {list.map((m) => {
              const disabled = blocked(m);
              return (
                <label
                  key={m.id}
                  className={
                    "flex items-start gap-2 rounded-lg border p-2.5 cursor-pointer transition-colors " +
                    (selected.has(m.id)
                      ? "border-accent/60 bg-accent/10"
                      : "border-white/10 bg-white/[0.03] hover:border-white/25") +
                    (disabled ? " opacity-40 cursor-not-allowed" : "")
                  }
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    disabled={disabled}
                    checked={selected.has(m.id)}
                    onChange={() => !disabled && toggle(m.id)}
                  />
                  <div className="min-w-0">
                    <div className="text-xs font-medium truncate">{m.label}</div>
                    <div className="text-[10px] text-white/40">
                      {m.provider} · ~${m.est_cost_usd.toFixed(2)} · {m.input}
                      {disabled &&
                        (needsAudio(m) && !audioUrl
                          ? needsImage(m) && !imageUrl
                            ? " · needs image + audio"
                            : " · needs audio"
                          : " · needs image")}
                    </div>
                    <div className="text-[10px] text-white/30 leading-snug mt-0.5">{m.note}</div>
                  </div>
                </label>
              );
            })}
          </div>
        </div>
      ))}

      {/* Run bar */}
      <div className="sticky bottom-4 flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-[#12121c]/95 backdrop-blur px-4 py-3">
        <div className="text-xs text-white/50">
          {selected.size} selected · est.{" "}
          <span className="text-white/80">${totalCost.toFixed(2)}</span> this run
        </div>
        <button
          onClick={run}
          disabled={!selected.size || running || anyPending}
          className="text-sm font-medium px-4 py-2 rounded-lg bg-accent hover:bg-accent/90 disabled:opacity-40"
        >
          {running || anyPending ? "Running…" : "Run selected"}
        </button>
      </div>

      {/* Results */}
      {Object.keys(results).length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {models
            .filter((m) => results[m.id])
            .map((m) => {
              const r = results[m.id];
              return (
                <div key={m.id} className="rounded-xl border border-white/10 bg-white/[0.03] overflow-hidden">
                  <div className="px-3 py-2 border-b border-white/10 flex items-center justify-between gap-2">
                    <span className="text-[11px] font-medium truncate">{m.label}</span>
                    <span className="text-[10px] text-white/30 shrink-0">
                      {r.elapsed ? `${r.elapsed.toFixed(0)}s` : ""} · ~${(r.cost ?? m.est_cost_usd).toFixed(2)}
                    </span>
                  </div>
                  <div className="aspect-[9/16] bg-black flex items-center justify-center">
                    {r.state === "done" && r.url ? (
                      m.kind !== "image" ? (
                        <video src={r.url} controls loop className="w-full h-full object-contain" />
                      ) : (
                        <img src={r.url} alt="" className="w-full h-full object-contain" />
                      )
                    ) : r.state === "error" ? (
                      <div className="p-3 text-[10px] text-red-300/80 text-center break-words">{r.error}</div>
                    ) : (
                      <div className="text-[11px] text-white/40 animate-pulse">
                        {r.state === "queued" ? "queued…" : "generating…"}
                      </div>
                    )}
                  </div>
                  {r.state === "done" && r.url && (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="block px-3 py-1.5 text-[10px] text-accent/80 hover:text-accent border-t border-white/10"
                    >
                      open ↗
                    </a>
                  )}
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}

export default PlaygroundPage;
