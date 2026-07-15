import { useState, useRef, useEffect } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Loader2,
  Wand2,
  Plus,
  GripVertical,
  Clock,
  Type,
  Package,
  AlertTriangle,
  Trash2,
  ImageIcon,
  Mic,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { scriptApi, castsApi, avatarApi } from "@/lib/api";
import { RewriteDiffModal } from "@/components/RewriteDiffModal";
import { BlockType } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import { DndContext, closestCenter, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy, useSortable, arrayMove } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

interface ScriptVariant {
  id: string;
  label: string;
  script_text: string;
  motion_prompt?: string;
}

interface ScriptBlock {
  id: string;
  type: BlockType;
  position: number;
  product_id?: string;
  product_name?: string;
  is_active?: boolean;
  background_id?: string | null;
  variants: ScriptVariant[];
}

interface ScriptBlockEditorProps {
  castId: string;
  blocks: ScriptBlock[];
  onBlocksChange: (blocks: ScriptBlock[]) => void;
  availableProducts?: Array<{ id: string; name: string; cover_image_url?: string }>;
  avatarId?: string | null;
  backgrounds?: Array<{ id: string; name: string; thumbnail_url: string }>;
  voiceCorpusCount?: number;
}

const BLOCK_TYPE_COLORS: Record<string, string> = {
  intro: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  product: "bg-green-500/20 text-green-400 border-green-500/30",
  cta: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  flash_sale: "bg-red-500/20 text-red-400 border-red-500/30",
  social_proof: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  filler: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  closing: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  idle: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

function estimateDuration(text: string): number {
  return Math.ceil(text.length / 10);
}

function AutoResizeTextarea({
  value,
  onChange,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = el.scrollHeight + "px";
    }
  }, [value]);

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={onChange}
      className="w-full resize-none rounded-md border border-border bg-card p-3 text-sm text-text focus:border-accent focus:outline-hidden"
      rows={3}
      {...props}
    />
  );
}

function SortableBlockCard({
  block,
  castId,
  onUpdate,
  onDelete,
  onToggleActive,
  availableProducts,
  backgrounds,
  voiceCorpusCount,
}: {
  block: ScriptBlock;
  castId: string;
  onUpdate: (updated: ScriptBlock) => void;
  onDelete: (blockId: string) => void;
  onToggleActive: (blockId: string, isActive: boolean) => void;
  availableProducts?: Array<{ id: string; name: string; cover_image_url?: string }>;
  backgrounds?: Array<{ id: string; name: string; thumbnail_url: string }>;
  voiceCorpusCount?: number;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: block.id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : (block.is_active !== false ? 1 : 0.45),
  };

  const [activeVariant, setActiveVariant] = useState(0);
  const [rewritePrompt, setRewritePrompt] = useState("");
  const [voiceDiff, setVoiceDiff] = useState<{ original: string; rewritten: string } | null>(null);

  // Ensure there's always at least one variant so textareas render even for empty blocks
  const effectiveVariants = block.variants.length > 0
    ? block.variants
    : [{ id: `placeholder_${block.id}`, label: "Variant A", script_text: "", motion_prompt: "" }];
  const variant = effectiveVariants[activeVariant] || effectiveVariants[0];
  const colorClass = BLOCK_TYPE_COLORS[block.type] || BLOCK_TYPE_COLORS.filler;

  const rewriteMutation = useMutation({
    mutationFn: () =>
      scriptApi.rewrite(castId, block.id, variant.id, rewritePrompt),
    onSuccess: (data) => {
      const updatedVariants = [...block.variants];
      updatedVariants[activeVariant] = {
        ...updatedVariants[activeVariant],
        script_text: data.script_text,
      };
      onUpdate({ ...block, variants: updatedVariants });
      setRewritePrompt("");
      toast({ title: "Script rewritten", variant: "success" });
    },
    onError: (e: any) =>
      toast({
        title: "Rewrite failed",
        description: e?.response?.data?.detail || "Try again",
        variant: "destructive",
      }),
  });

  const voiceRewriteMutation = useMutation({
    mutationFn: () => scriptApi.rewriteInVoice(castId, block.id),
    onSuccess: (data: { original: string; rewritten: string }) => {
      setVoiceDiff(data);
    },
    onError: (e: any) =>
      toast({
        title: "Voice rewrite failed",
        description: e?.response?.data?.detail || "Try again",
        variant: "destructive",
      }),
  });

  const handleAcceptVoiceRewrite = (text: string) => {
    const updatedVariants = [...block.variants];
    updatedVariants[activeVariant] = {
      ...updatedVariants[activeVariant],
      script_text: text,
    };
    onUpdate({ ...block, variants: updatedVariants });
    setVoiceDiff(null);
    toast({ title: "Voice rewrite applied", variant: "success" });
  };

  const handleTextChange = (text: string) => {
    const updatedVariants = block.variants.length > 0
      ? [...block.variants]
      : [{ id: `var_${block.id}_a`, label: "Variant A", script_text: "" }];
    updatedVariants[activeVariant] = {
      ...updatedVariants[activeVariant],
      script_text: text,
    };
    onUpdate({ ...block, variants: updatedVariants });
  };

  const charCount = variant?.script_text.length ?? 0;

  const handleDeleteClick = () => {
    if (!window.confirm("Delete this block? This cannot be undone.")) return;
    onDelete(block.id);
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="rounded-lg border border-border bg-surface p-4"
      data-testid={`script-block-${block.id}`}
    >
      {/* Header */}
      <div className="mb-3 flex items-center gap-2">
        <button {...attributes} {...listeners} className="cursor-grab active:cursor-grabbing py-1 touch-none">
          <GripVertical className="h-4 w-4 text-text-muted" />
        </button>
        <input
          type="checkbox"
          checked={block.is_active !== false}
          onChange={(e) => onToggleActive(block.id, e.target.checked)}
          className="mt-0.5 accent-accent"
          title={block.is_active !== false ? "Active — included in cast" : "Skipped — not included"}
        />
        <Badge className={cn("text-[10px] border", colorClass)}>
          {block.type.replace("_", " ")}
        </Badge>
        {block.product_name && !availableProducts?.length && (
          <span className="text-xs text-accent">{block.product_name}</span>
        )}
        <span className="text-[10px] text-text-muted">#{block.position + 1}</span>
        <div className="flex-1" />
        <button
          onClick={handleDeleteClick}
          className="text-text-muted hover:text-red-400 transition-colors"
          title="Delete block"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {/* Product selector */}
      {availableProducts && availableProducts.length > 0 && (
        <div className="mb-3">
          <div className="flex items-center gap-2">
            <Package className="h-3 w-3 text-text-muted" />
            <select
              value={block.product_id || ""}
              onChange={(e) => {
                onUpdate({ ...block, product_id: e.target.value || undefined });
              }}
              className="h-7 flex-1 rounded border border-border bg-bg px-2 text-xs text-text"
            >
              <option value="">— No product —</option>
              {availableProducts.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          {["product", "flash_sale", "cta"].includes(block.type) && !block.product_id && (
            <div className="mt-1 flex items-center gap-1 text-[10px] text-warning">
              <AlertTriangle className="h-3 w-3" />
              No product assigned — overlay won't render
            </div>
          )}
        </div>
      )}

      {/* Background selector */}
      {backgrounds && backgrounds.length > 0 && (
        <div className="mb-3">
          <div className="flex items-center gap-2">
            <ImageIcon className="h-3 w-3 text-text-muted" />
            <select
              value={block.background_id || ""}
              onChange={(e) => {
                onUpdate({ ...block, background_id: e.target.value || null });
              }}
              className="h-7 flex-1 rounded border border-border bg-bg px-2 text-xs text-text"
            >
              <option value="">— Default scene —</option>
              {backgrounds.map((bg) => (
                <option key={bg.id} value={bg.id}>{bg.name}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Variant pills */}
      {block.variants.length > 1 && (
        <div className="mb-3 flex gap-1">
          {block.variants.map((v, i) => (
            <button
              key={v.id}
              onClick={() => setActiveVariant(i)}
              className={cn(
                "rounded-full px-3 py-1 text-[10px] font-medium transition-colors",
                i === activeVariant
                  ? "bg-accent text-white"
                  : "bg-surface text-text-dim hover:bg-card border border-border",
              )}
              data-testid={`variant-pill-${v.id}`}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}

      {/* Script textarea */}
      {variant && (
        <>
          <label className="text-[10px] text-text-muted mb-1 block">Spoken Text</label>
          <AutoResizeTextarea
            value={variant.script_text}
            onChange={(e) => handleTextChange(e.target.value)}
            placeholder="One punchy hook, 30-40 words max. TikTok pacing."
            data-testid={`script-textarea-${variant.id}`}
          />

          <label className="text-[10px] text-text-muted mb-1 mt-3 block">Motion & Gestures</label>
          <AutoResizeTextarea
            value={variant.motion_prompt || ""}
            onChange={(e) => {
              const updatedVariants = block.variants.length > 0
                ? [...block.variants]
                : [{ id: `var_${block.id}_a`, label: "Variant A", script_text: "" }];
              updatedVariants[activeVariant] = {
                ...updatedVariants[activeVariant],
                motion_prompt: e.target.value,
              };
              onUpdate({ ...block, variants: updatedVariants });
            }}
            placeholder="e.g. waving warmly, animated gestures, bright smile"
            data-testid={`motion-textarea-${variant.id}`}
          />

          {/* Metadata */}
          {(() => {
            const wordCount = variant.script_text.split(/\s+/).filter(Boolean).length;
            const estSpeech = Math.round(wordCount * 0.4);
            const counterColor = charCount >= 280 ? "text-red-400" : charCount >= 250 ? "text-amber-400" : "text-text-muted";
            return (
              <div className={`mt-2 flex items-center gap-3 text-[10px] ${counterColor}`}>
                <span className="flex items-center gap-1">
                  <Type className="h-3 w-3" />
                  {charCount}/280 chars
                </span>
                <span>~{wordCount} words</span>
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  ~{estSpeech}s speech
                </span>
              </div>
            );
          })()}

          {/* AI rewrite bar */}
          <div className="mt-3 flex gap-2">
            <Input
              value={rewritePrompt}
              onChange={(e) => setRewritePrompt(e.target.value)}
              placeholder="e.g., make it shorter, add urgency, translate to Spanish..."
              className="text-xs"
              onKeyDown={(e) =>
                e.key === "Enter" &&
                rewritePrompt.trim() &&
                rewriteMutation.mutate()
              }
              data-testid={`rewrite-input-${block.id}`}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!rewritePrompt.trim() || rewriteMutation.isPending}
              onClick={() => rewriteMutation.mutate()}
              data-testid={`rewrite-button-${block.id}`}
            >
              {rewriteMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <><Wand2 className="mr-1 h-3 w-3" /> Rewrite</>
              )}
            </Button>
          </div>

          {/* Rewrite in my voice button */}
          <div className="mt-2">
            <Button
              size="sm"
              variant="outline"
              className="text-xs border-purple-500/30 text-purple-400 hover:bg-purple-500/10"
              disabled={!variant?.script_text?.trim() || voiceRewriteMutation.isPending || !(voiceCorpusCount && voiceCorpusCount > 0)}
              onClick={() => voiceRewriteMutation.mutate()}
              title={voiceCorpusCount && voiceCorpusCount > 0 ? "Rewrite using your voice examples" : "Add voice examples on your avatar profile to enable this"}
              data-testid={`rewrite-voice-button-${block.id}`}
            >
              {voiceRewriteMutation.isPending ? (
                <><Loader2 className="mr-1 h-3 w-3 animate-spin" /> Rewriting...</>
              ) : (
                <><Mic className="mr-1 h-3 w-3" /> Rewrite in my voice</>
              )}
            </Button>
          </div>

          {/* Voice diff modal */}
          {voiceDiff && (
            <RewriteDiffModal
              open={!!voiceDiff}
              onClose={() => setVoiceDiff(null)}
              original={voiceDiff.original}
              rewritten={voiceDiff.rewritten}
              onAccept={handleAcceptVoiceRewrite}
            />
          )}
        </>
      )}
    </div>
  );
}

export function ScriptBlockEditor({
  castId,
  blocks,
  onBlocksChange,
  availableProducts,
  avatarId,
  backgrounds,
  voiceCorpusCount,
}: ScriptBlockEditorProps) {
  // Fetch avatar backgrounds
  const { data: avatarBackgrounds } = useQuery({
    queryKey: ["avatar-backgrounds", avatarId],
    queryFn: async () => {
      if (!avatarId) return [];
      const res = await avatarApi.list() as any;
      const avatar = (res.avatars || []).find((a: any) => a.id === avatarId);
      return (avatar?.backgrounds || []).map((bg: any) => ({
        id: bg.id,
        name: bg.name || bg.label || "Scene",
        thumbnail_url: bg.thumbnail_url || bg.url || "",
      }));
    },
    enabled: !!avatarId,
  });

  const handleUpdateBlock = (updated: ScriptBlock) => {
    onBlocksChange(blocks.map((b) => (b.id === updated.id ? updated : b)));
  };

  const handleAddBlock = (type: BlockType) => {
    const newBlock: ScriptBlock = {
      id: `blk_${Date.now()}`,
      type,
      position: blocks.length,
      is_active: true,
      variants: [
        {
          id: `var_${Date.now()}_a`,
          label: "Variant A",
          script_text: "",
        },
      ],
    };
    onBlocksChange([...blocks, newBlock]);
  };

  const handleDeleteBlock = (blockId: string) => {
    onBlocksChange(blocks.filter((b) => b.id !== blockId));
    castsApi.deleteBlock(castId, blockId).catch(() => {});
  };

  const handleToggleActive = (blockId: string, isActive: boolean) => {
    onBlocksChange(blocks.map((b) => b.id === blockId ? { ...b, is_active: isActive } : b));
    castsApi.toggleBlockActive(castId, blockId, isActive).catch(() => {});
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = blocks.findIndex((b) => b.id === active.id);
    const newIndex = blocks.findIndex((b) => b.id === over.id);
    const newBlocks = arrayMove(blocks, oldIndex, newIndex).map((b, i) => ({ ...b, position: i }));
    onBlocksChange(newBlocks);
    castsApi.reorderBlocks(castId, newBlocks.map((b) => b.id)).catch(() => {});
  };

  const totalDuration = blocks.reduce((sum, b) => {
    const bestVariant = b.variants[0];
    return sum + (bestVariant ? estimateDuration(bestVariant.script_text) : 0);
  }, 0);

  return (
    <div className="space-y-4" data-testid="script-block-editor">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text">Script Blocks</h3>
        <span className="text-xs text-text-muted">
          {blocks.length} blocks · ~{Math.ceil(totalDuration / 60)} min estimated
        </span>
      </div>

      {/* Block list with DnD */}
      <DndContext collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={blocks.map((b) => b.id)} strategy={verticalListSortingStrategy}>
          <div className="relative space-y-3">
            {blocks.length > 1 && (
              <div className="absolute left-5 top-4 bottom-4 w-px bg-border" />
            )}
            {blocks.map((block) => (
              <SortableBlockCard
                key={block.id}
                block={block}
                castId={castId}
                onUpdate={handleUpdateBlock}
                onDelete={handleDeleteBlock}
                onToggleActive={handleToggleActive}
                availableProducts={availableProducts}
                backgrounds={avatarBackgrounds}
                voiceCorpusCount={voiceCorpusCount}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {/* Add block */}
      <div className="flex flex-wrap gap-2">
        <span className="text-xs text-text-muted self-center">Add block:</span>
        {[BlockType.PRODUCT, BlockType.CTA, BlockType.SOCIAL_PROOF, BlockType.FILLER].map(
          (type) => (
            <Button
              key={type}
              size="sm"
              variant="outline"
              className="text-xs"
              onClick={() => handleAddBlock(type)}
              data-testid={`add-block-${type}`}
            >
              <Plus className="mr-1 h-3 w-3" />
              {type.replace("_", " ")}
            </Button>
          ),
        )}
      </div>
    </div>
  );
}

export type { ScriptBlock, ScriptVariant };
