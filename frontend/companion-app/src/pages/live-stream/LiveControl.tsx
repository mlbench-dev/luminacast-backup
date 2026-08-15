import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Radio,
  Play,
  Pause,
  Square,
  Copy,
  Check,
  Plus,
  Trash2,
  Clock,
  AlertCircle,
  Loader2,
  Globe,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { liveSessionApi, api, stockMediaApi } from "@/lib/api";
import { StockMediaPicker } from "@/components/common/StockMediaPicker";
import type { StockMediaItem } from "@/components/common/StockMediaPicker";
import { toast } from "@/hooks/useToast";
import Hls from "hls.js";

// ── Types ──

interface ProductQueueItem {
  product_id: string;
  footage_keys: string[];
  talking_points: string;
}

interface LiveSession {
  id: string;
  user_id: string;
  avatar_id: string;
  title: string | null;
  status: string;
  product_queue: ProductQueueItem[];
  voice_style_notes: string | null;
  max_duration_minutes: number;
  output_format: string;
  current_product_index: number;
  current_paragraph: string | null;
  stream_key: string | null;
  hls_url: string | null;
  started_at: string | null;
  ended_at: string | null;
  total_paragraphs_generated: number;
  error_message: string | null;
  created_at: string | null;
}

// ── Main Page ──

export function LiveControlPage() {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"setup" | "dashboard">("setup");
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  // Fetch user's live sessions
  const { data: sessionsData, isLoading: loadingSessions } = useQuery({
    queryKey: ["live-sessions"],
    queryFn: () => liveSessionApi.list(),
    refetchInterval: 5000,
  });

  const sessions: LiveSession[] = (sessionsData as any)?.sessions ?? [];
  const activeSession = sessions.find(
    (s) => s.status === "live" || s.status === "starting" || s.status === "paused"
  );

  useEffect(() => {
    if (activeSession) {
      setActiveSessionId(activeSession.id);
      setMode("dashboard");
    }
  }, [activeSession]);

  if (loadingSessions) {
    return (
      <div className="space-y-4" data-testid="live-control-page">
        <h1 className="text-2xl font-bold text-text">Go Live</h1>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (mode === "dashboard" && activeSessionId) {
    return (
      <LiveDashboard
        sessionId={activeSessionId}
        onEnded={() => {
          setMode("setup");
          setActiveSessionId(null);
          queryClient.invalidateQueries({ queryKey: ["live-sessions"] });
        }}
      />
    );
  }

  return (
    <SessionSetupForm
      onCreated={(id) => {
        setActiveSessionId(id);
        setMode("dashboard");
        queryClient.invalidateQueries({ queryKey: ["live-sessions"] });
      }}
      pastSessions={sessions.filter((s) => s.status === "ended" || s.status === "failed")}
    />
  );
}

// ── Session Setup Form ──

function SessionSetupForm({
  onCreated,
  pastSessions,
}: {
  onCreated: (id: string) => void;
  pastSessions: LiveSession[];
}) {
  const [avatarId, setAvatarId] = useState("");
  const [title, setTitle] = useState("");
  const [voiceStyle, setVoiceStyle] = useState("");
  const [maxDuration, setMaxDuration] = useState(60);
  const [outputFormat, setOutputFormat] = useState("9:16");
  const [productQueue, setProductQueue] = useState<ProductQueueItem[]>([]);
  const [newProductId, setNewProductId] = useState("");
  const [newTalkingPoints, setNewTalkingPoints] = useState("");

  // Fetch avatars
  const { data: avatarsData } = useQuery({
    queryKey: ["avatars"],
    queryFn: () => {
      
      return api.get("/avatar").then((r) => r.data);
    },
  });
  const avatars = (avatarsData?.avatars ?? []).filter(
    (a: any) => a.status === "approved" || a.status === "ready" || a.id === "default"
  );

  // Fetch products
  const { data: productsData } = useQuery({
    queryKey: ["products-for-live"],
    queryFn: () => {
      
      return api.get("/products").then((r) => r.data);
    },
  });
  const products = productsData?.products ?? [];

  const createMutation = useMutation({
    mutationFn: async () => {
      const session = await liveSessionApi.create({
        avatar_id: avatarId,
        title: title || undefined,
        product_queue: productQueue,
        voice_style_notes: voiceStyle || undefined,
        max_duration_minutes: maxDuration,
        output_format: outputFormat,
      });
      // Auto-start
      const started = await liveSessionApi.start(session.id);
      return started;
    },
    onSuccess: (data) => {
      toast({ title: "Live session started!", variant: "success" });
      onCreated(data.id);
    },
    onError: (err: any) => {
      toast({
        title: "Failed to create session",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    },
  });

  const [stockPickerOpen, setStockPickerOpen] = useState(false);
  const [stockPickerIdx, setStockPickerIdx] = useState<number>(-1);
  const [importingStockIdx, setImportingStockIdx] = useState<number>(-1);

  const handleStockSelect = async (media: StockMediaItem, idx: number) => {
    setImportingStockIdx(idx);
    try {
      const result = await stockMediaApi.importMedia({
        url: media.src,
        type: media.type,
        pexels_id: media.id,
        name: "Pexels " + media.type + " by " + media.photographer,
      });
      setProductQueue((q) =>
        q.map((item, i) =>
          i === idx
            ? { ...item, footage_keys: [...item.footage_keys, result.r2_key] }
            : item
        )
      );
      toast({ title: "Stock footage added", variant: "success" });
    } catch (err: any) {
      toast({
        title: "Import failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setImportingStockIdx(-1);
    }
  };

  const addProduct = () => {
    if (!newProductId) return;
    setProductQueue((q) => [
      ...q,
      { product_id: newProductId, footage_keys: [], talking_points: newTalkingPoints },
    ]);
    setNewProductId("");
    setNewTalkingPoints("");
  };

  const removeProduct = (idx: number) => {
    setProductQueue((q) => q.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-6" data-testid="live-control-page">
      <div>
        <h1 className="text-2xl font-bold text-text">Go Live</h1>
        <p className="text-sm text-text-dim">
          Set up a voice-only live broadcast. Your AI voice narrates products over footage.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Session Setup</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Title */}
          <div>
            <label className="mb-1 block text-sm font-medium text-text-dim">Session Title</label>
            <Input
              placeholder="Evening Product Show"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              data-testid="session-title"
            />
          </div>

          {/* Avatar picker */}
          <div>
            <label className="mb-1 block text-sm font-medium text-text-dim">Pick Avatar</label>
            <select
              value={avatarId}
              onChange={(e) => setAvatarId(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
              data-testid="avatar-picker"
            >
              <option value="">Select an avatar...</option>
              {avatars.map((a: any) => (
                <option key={a.id} value={a.id}>
                  {a.persona_profile?.name || a.source_handle || a.id}
                  {a.voice_id ? " (voice clone)" : ""}
                </option>
              ))}
            </select>
          </div>

          {/* Product Queue */}
          <div>
            <label className="mb-1 block text-sm font-medium text-text-dim">Product Queue</label>
            {productQueue.length > 0 && (
              <div className="mb-2 space-y-2">
                {productQueue.map((item, idx) => {
                  const prod = products.find((p: any) => p.id === item.product_id);
                  return (
                    <div
                      key={idx}
                      className="flex items-center gap-2 rounded border border-border bg-surface/50 px-3 py-2"
                    >
                      <span className="text-xs font-medium text-text-dim">#{idx + 1}</span>
                      <span className="flex-1 text-sm text-text">
                        {prod?.name || item.product_id}
                      </span>
                      {item.talking_points && (
                        <span className="text-xs text-text-muted">{item.talking_points.slice(0, 40)}...</span>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setStockPickerIdx(idx);
                          setStockPickerOpen(true);
                        }}
                        disabled={importingStockIdx === idx}
                        className="h-6 text-xs"
                        data-testid={"stock-footage-btn-" + idx}
                      >
                        {importingStockIdx === idx ? (
                          <Loader2 className="h-3 w-3 animate-spin mr-1" />
                        ) : (
                          <Globe className="h-3 w-3 mr-1" />
                        )}
                        {item.footage_keys.length > 0
                          ? item.footage_keys.length + " clip(s)"
                          : "Stock"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => removeProduct(idx)}
                        className="h-6 w-6"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="flex gap-2">
              <select
                value={newProductId}
                onChange={(e) => setNewProductId(e.target.value)}
                className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                data-testid="product-picker"
              >
                <option value="">Select product...</option>
                {products.map((p: any) => (
                  <option key={p.id} value={p.id}>
                    {p.name} — ${p.price || p.current_price || "?"}
                  </option>
                ))}
              </select>
              <Input
                placeholder="Talking points..."
                value={newTalkingPoints}
                onChange={(e) => setNewTalkingPoints(e.target.value)}
                className="flex-1"
              />
              <Button variant="outline" size="sm" onClick={addProduct} disabled={!newProductId}>
                <Plus className="mr-1 h-3 w-3" /> Add Product
              </Button>
            </div>
          </div>

          {/* Voice style */}
          <div>
            <label className="mb-1 block text-sm font-medium text-text-dim">Voice Style Notes</label>
            <textarea
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
              rows={2}
              placeholder="energetic and casual, use slang, hype the products..."
              value={voiceStyle}
              onChange={(e) => setVoiceStyle(e.target.value)}
              data-testid="voice-style"
            />
          </div>

          {/* Duration + Format */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-text-dim">Max Duration</label>
              <select
                value={maxDuration}
                onChange={(e) => setMaxDuration(Number(e.target.value))}
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                data-testid="duration-select"
              >
                <option value={30}>30 minutes</option>
                <option value={60}>60 minutes</option>
                <option value={120}>120 minutes</option>
                <option value={240}>240 minutes</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-text-dim">Output Format</label>
              <select
                value={outputFormat}
                onChange={(e) => setOutputFormat(e.target.value)}
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                data-testid="output-format"
              >
                <option value="9:16">9:16 (Portrait)</option>
                <option value="16:9">16:9 (Landscape)</option>
                <option value="1:1">1:1 (Square)</option>
              </select>
            </div>
          </div>

          <StockMediaPicker
            open={stockPickerOpen}
            onClose={() => setStockPickerOpen(false)}
            onSelect={(media) => handleStockSelect(media, stockPickerIdx)}
            mediaType="video"
          />

          {/* Start button */}
          <Button
            onClick={() => createMutation.mutate()}
            disabled={!avatarId || productQueue.length === 0 || createMutation.isPending}
            className="w-full"
            data-testid="create-session-button"
          >
            {createMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Starting...
              </>
            ) : (
              <>
                <Radio className="mr-2 h-4 w-4" />
                Create Session & Go Live
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Past sessions */}
      {pastSessions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Past Sessions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {pastSessions.slice(0, 5).map((s) => (
                <div key={s.id} className="flex items-center justify-between rounded border border-border px-3 py-2">
                  <div>
                    <span className="text-sm text-text">{s.title || s.id}</span>
                    <span className="ml-2 text-xs text-text-muted">
                      {s.total_paragraphs_generated} paragraphs
                    </span>
                  </div>
                  <Badge variant={s.status === "ended" ? "secondary" : "danger"}>
                    {s.status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ── Live Dashboard ──

function LiveDashboard({
  sessionId,
  onEnded,
}: {
  sessionId: string;
  onEnded: () => void;
}) {
  const queryClient = useQueryClient();
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const [copied, setCopied] = useState(false);

  // Poll session status
  const { data: session } = useQuery<LiveSession>({
    queryKey: ["live-session", sessionId],
    queryFn: () => liveSessionApi.get(sessionId),
    refetchInterval: 3000,
  });

  const status = session?.status ?? "loading";
  const isActive = status === "live" || status === "starting" || status === "paused";

  // Redirect back to setup when ended
  useEffect(() => {
    if (session && !isActive && status !== "loading") {
      onEnded();
    }
  }, [session, isActive, status, onEnded]);

  // Get stream URL
  const { data: streamData } = useQuery({
    queryKey: ["live-session-stream", sessionId],
    queryFn: () => liveSessionApi.streamUrl(sessionId),
    enabled: status === "live" || status === "starting",
    refetchInterval: 10000,
  });
  const hlsUrl = streamData?.hls_url;

  // HLS.js player
  useEffect(() => {
    if (!hlsUrl || !videoRef.current) return;
    if (status !== "live") return;

    if (Hls.isSupported()) {
      const hls = new Hls({
        liveSyncDurationCount: 3,
        liveMaxLatencyDurationCount: 6,
        enableWorker: true,
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(videoRef.current);
      hlsRef.current = hls;
      return () => {
        hls.destroy();
        hlsRef.current = null;
      };
    } else if (videoRef.current.canPlayType("application/vnd.apple.mpegurl")) {
      videoRef.current.src = hlsUrl;
    }
  }, [hlsUrl, status]);

  // Mutations
  const pauseMut = useMutation({
    mutationFn: () => liveSessionApi.pause(sessionId),
    onSuccess: () => {
      toast({ title: "Session paused" });
      queryClient.invalidateQueries({ queryKey: ["live-session", sessionId] });
    },
  });

  const resumeMut = useMutation({
    mutationFn: () => liveSessionApi.resume(sessionId),
    onSuccess: () => {
      toast({ title: "Session resumed" });
      queryClient.invalidateQueries({ queryKey: ["live-session", sessionId] });
    },
  });

  const stopMut = useMutation({
    mutationFn: () => liveSessionApi.stop(sessionId),
    onSuccess: () => {
      toast({ title: "Session ended" });
      queryClient.invalidateQueries({ queryKey: ["live-sessions"] });
      onEnded();
    },
  });

  const copyUrl = useCallback(() => {
    if (hlsUrl) {
      navigator.clipboard.writeText(hlsUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [hlsUrl]);

  // Elapsed time
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!session?.started_at || !isActive) return;
    const start = new Date(session.started_at).getTime();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [session?.started_at, isActive]);

  const formatTime = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div className="space-y-4" data-testid="live-control-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-text">
            {session?.title || "Live Session"}
          </h1>
          <Badge
            variant={
              status === "live" ? "danger" : status === "paused" ? "secondary" : "default"
            }
            data-testid="session-status"
          >
            {status === "live" && "LIVE"}
            {status === "starting" && "Starting..."}
            {status === "paused" && "Paused"}
            {status === "ended" && "Ended"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {status === "live" && (
            <Button
              variant="outline"
              onClick={() => pauseMut.mutate()}
              disabled={pauseMut.isPending}
            >
              <Pause className="mr-1 h-4 w-4" /> Pause
            </Button>
          )}
          {status === "paused" && (
            <Button
              variant="outline"
              onClick={() => resumeMut.mutate()}
              disabled={resumeMut.isPending}
            >
              <Play className="mr-1 h-4 w-4" /> Resume
            </Button>
          )}
          {isActive && (
            <Button
              variant="destructive"
              onClick={() => stopMut.mutate()}
              disabled={stopMut.isPending}
            >
              <Square className="mr-1 h-4 w-4" /> End Session
            </Button>
          )}
        </div>
      </div>

      {/* Stream URL */}
      {hlsUrl && (
        <Card>
          <CardContent className="flex items-center gap-3 py-3">
            <div className="flex-1">
              <p className="text-xs font-medium text-text-dim">Stream URL (paste in OBS Media Source)</p>
              <code className="mt-0.5 block text-sm text-text">{hlsUrl}</code>
            </div>
            <Button variant="outline" size="sm" onClick={copyUrl}>
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Instructions */}
      {status === "starting" && (
        <Card className="border-warning/50 bg-warning/5">
          <CardContent className="py-3">
            <div className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin text-warning" />
              <span className="text-sm text-warning">
                Generating first paragraph... Stream will start shortly.
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {status === "live" && !session?.total_paragraphs_generated && (
        <Card className="border-primary/50 bg-primary/5">
          <CardContent className="py-3">
            <p className="text-sm text-text-dim">
              Open OBS, add a Media Source, paste the stream URL above, then Start Streaming to TikTok.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Left: Preview + Script */}
        <div className="space-y-4 lg:col-span-2">
          {/* HLS Preview */}
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Stream Preview</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="relative aspect-9/16 max-h-[500px] overflow-hidden rounded-lg bg-black">
                <video
                  ref={videoRef}
                  autoPlay
                  muted
                  playsInline
                  className="h-full w-full object-contain"
                  data-testid="hls-preview"
                />
                {status !== "live" && (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-sm text-white/50">
                      {status === "starting" ? "Waiting for first segment..." : "No preview"}
                    </span>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Current script paragraph */}
          {session?.current_paragraph && (
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Current Script</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm italic text-text-dim" data-testid="current-paragraph">
                  "{session.current_paragraph}"
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: Stats */}
        <div className="space-y-4">
          {/* Stats */}
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Session Stats</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <StatRow
                icon={<Clock className="h-4 w-4 text-text-muted" />}
                label="Elapsed"
                value={formatTime(elapsed)}
              />
              <StatRow
                icon={<Radio className="h-4 w-4 text-text-muted" />}
                label="Paragraphs"
                value={String(session?.total_paragraphs_generated ?? 0)}
              />
              <StatRow
                icon={<AlertCircle className="h-4 w-4 text-text-muted" />}
                label="Product #"
                value={`${(session?.current_product_index ?? 0) + 1} / ${session?.product_queue?.length ?? 0}`}
              />
              <StatRow
                icon={<Clock className="h-4 w-4 text-text-muted" />}
                label="Max Duration"
                value={`${session?.max_duration_minutes ?? 60} min`}
              />
            </CardContent>
          </Card>

          {/* Product queue */}
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Product Queue</CardTitle>
            </CardHeader>
            <CardContent>
              {(session?.product_queue ?? []).map((item, idx) => (
                <div
                  key={idx}
                  className={`flex items-center gap-2 rounded px-2 py-1.5 text-sm ${
                    idx === (session?.current_product_index ?? 0) % (session?.product_queue?.length ?? 1)
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-text-dim"
                  }`}
                >
                  <span className="text-xs">{idx + 1}.</span>
                  <span className="flex-1 truncate">{item.product_id}</span>
                  {idx === (session?.current_product_index ?? 0) % (session?.product_queue?.length ?? 1) && (
                    <Badge variant="default" className="text-[10px]">
                      NOW
                    </Badge>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Error */}
          {session?.error_message && (
            <Card className="border-danger/50">
              <CardContent className="py-3">
                <p className="text-sm text-danger">{session.error_message}</p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function StatRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-sm text-text-dim">{label}</span>
      </div>
      <span className="text-sm font-semibold text-text" data-testid={`stat-${label.toLowerCase()}`}>
        {value}
      </span>
    </div>
  );
}
