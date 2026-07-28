import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { avatarLooksApi, productsApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cdnUrl } from "@/lib/cdn";
import { Search, ShoppingBag } from "lucide-react";
import type { ProductWithAssets } from "@/lib/types";

const PRESET_BACKGROUNDS: { label: string; prompt: string }[] = [
  { label: "Studio Gray", prompt: "professional studio with seamless gray backdrop, soft directional lighting" },
  { label: "White Seamless", prompt: "pure white seamless studio backdrop, clean professional lighting" },
  { label: "Modern Office", prompt: "modern bright office interior, large windows, warm natural light" },
  { label: "Outdoor Daylight", prompt: "outdoor natural setting, soft daylight, blurred green background" },
  { label: "Beach Sunset", prompt: "beach at sunset, warm golden hour light, blurred ocean background" },
  { label: "Neon City", prompt: "neon-lit urban night scene, bokeh city lights in background" },
  { label: "Bookshelf", prompt: "cozy bookshelf background, warm library lighting, slightly blurred" },
  { label: "Pink Gradient", prompt: "smooth pink to purple gradient background, even soft lighting" },
  { label: "Plain Black", prompt: "pure black studio backdrop, dramatic single-source lighting" },
  { label: "Bokeh Lights", prompt: "soft bokeh light background, warm blurred lights, intimate atmosphere" },
  { label: "Brick Wall", prompt: "exposed red brick wall background, warm even lighting" },
  { label: "Garden", prompt: "lush green garden background, dappled natural sunlight, blurred foliage" },
];

const ENVIRONMENT_OPTIONS: { value: string; label: string }[] = [
  { value: "studio", label: "Studio" },
  { value: "room", label: "Room" },
  { value: "outdoor", label: "Outdoor" },
];

const POSE_OPTIONS: { value: string; label: string }[] = [
  { value: "front", label: "Front" },
  { value: "three_quarter_left", label: "Three Quarter Left" },
  { value: "three_quarter_right", label: "Three Quarter Right" },
  { value: "profile_left", label: "Profile Left" },
  { value: "profile_right", label: "Profile Right" },
  { value: "back", label: "Back" },
];

interface Props {
  avatarId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lookType?: "background" | "body_motion" | "tryon";
}

export function AddLookDialog({ avatarId, open, onOpenChange, lookType = "background" }: Props) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [selectedChip, setSelectedChip] = useState<string | null>(null);
  const [poseAngle, setPoseAngle] = useState("front");
  const [selectedProduct, setSelectedProduct] = useState<ProductWithAssets | null>(null);
  const [productSearch, setProductSearch] = useState("");
  const [environment, setEnvironment] = useState("studio");
  const [micVisible, setMicVisible] = useState(false);

  const reset = () => {
    setName("");
    setPrompt("");
    setSelectedChip(null);
    setPoseAngle("front");
    setSelectedProduct(null);
    setProductSearch("");
    setEnvironment("studio");
    setMicVisible(false);
  };
  const handleClose = () => { reset(); onOpenChange(false); };

  const handleChipClick = (preset: { label: string; prompt: string }) => {
    setPrompt(preset.prompt);
    // Keep the name in sync with the selected preset when switching presets.
    // Only preserve the name if the user has typed something of their own —
    // i.e. it no longer matches the previously selected preset's label.
    setName((prev) => (!prev.trim() || prev === selectedChip ? preset.label : prev));
    setSelectedChip(preset.label);
  };

  // Fetch products for tryon picker
  const { data: productsData } = useQuery({
    queryKey: ["products-for-tryon", productSearch],
    queryFn: () => productsApi.list({ search: productSearch || undefined, per_page: 50 }),
    enabled: lookType === "tryon" && open,
  });
  const products: ProductWithAssets[] = productsData?.products || [];

  // Check if avatar has front body motion look (required for try-on)
  const { data: looksData } = useQuery({
    queryKey: ["avatar-looks", avatarId],
    queryFn: () => avatarLooksApi.list(avatarId),
    enabled: lookType === "tryon" && open,
  });
  const hasFrontBodyMotion = (looksData?.looks || []).some(
    (l: any) => l.look_type === "body_motion" && l.pose_angle === "front" && l.status === "ready"
  );

  const handleSelectProduct = (product: ProductWithAssets) => {
    setSelectedProduct(product);
    setName(`Try on: ${product.name || "Product"}`);
  };

  const createMutation = useMutation({
    mutationFn: () => {
      if (lookType === "tryon") {
        if (!selectedProduct) throw new Error("Please select a product");
        return avatarLooksApi.create(avatarId, {
          name: name.trim() || `Try on: ${selectedProduct.name}`,
          background_prompt: "",
          look_type: "tryon",
          product_id: selectedProduct.id,
        });
      }
      const payload: { name: string; background_prompt?: string; look_type?: string; pose_angle?: string; environment?: string; mic_visible?: boolean } = {
        name: name.trim() || "New Look",
        background_prompt: prompt.trim() || (lookType === "body_motion" ? `Body motion pose: ${poseAngle}` : ""),
        look_type: lookType,
      };
      if (lookType === "body_motion") {
        payload.pose_angle = poseAngle;
      }
      if (lookType === "background") {
        // Scene properties decided now, at creation time, instead of only
        // per-block afterward — see services/mic_presets.py.
        payload.environment = environment;
        payload.mic_visible = micVisible;
      }
      return avatarLooksApi.create(avatarId, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] });
      toast({ title: "Look generation started", description: "This usually takes 30-60 seconds." });
      handleClose();
    },
    onError: (err: any) => {
      toast({
        title: "Failed to create look",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    },
  });

  // Try-on flow
  if (lookType === "tryon") {
    const filteredProducts = products.filter((p) => {
      if (!productSearch) return true;
      const q = productSearch.toLowerCase();
      return (p.name || "").toLowerCase().includes(q);
    });

    return (
      <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
        <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col bg-black text-text border-border">
          <DialogHeader>
            <DialogTitle>Generate Try-On Look</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 overflow-y-auto flex-1 pr-1">
            {/* Warning if no front body motion photo */}
            {!hasFrontBodyMotion && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-400">
                <strong>Front body photo required.</strong> Wardrobe Engine needs a full-body front photo for virtual try-on.
                Go to <strong>Body Motion</strong> tab and generate the <strong>Front</strong> pose first.
              </div>
            )}
            {/* Product search */}
            <div>
              <label className="text-sm text-text-muted mb-1 block">Select a product to try on</label>
              <div className="relative mb-2">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-text-muted" />
                <Input
                  value={productSearch}
                  onChange={(e) => setProductSearch(e.target.value)}
                  placeholder="Search products..."
                  className="pl-8 text-sm"
                />
              </div>
              {filteredProducts.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border py-6 text-center text-sm text-text-muted">
                  <ShoppingBag className="h-6 w-6 mx-auto mb-2 opacity-40" />
                  No products found. Add products to your library first.
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-2 max-h-48 overflow-y-auto">
                  {filteredProducts.map((product) => {
                    const imgUrl = product.cover_image_url || cdnUrl(product.cover_image_key) || null;
                    const isSelected = selectedProduct?.id === product.id;
                    return (
                      <button
                        key={product.id}
                        onClick={() => handleSelectProduct(product)}
                        className={`border rounded-lg overflow-hidden text-left transition-colors ${
                          isSelected
                            ? "border-accent ring-1 ring-accent"
                            : "border-border hover:border-accent/50"
                        }`}
                      >
                        <div className="aspect-square bg-surface">
                          {imgUrl ? (
                            <img src={imgUrl} alt={product.name} className="w-full h-full object-cover" />
                          ) : (
                            <div className="w-full h-full flex items-center justify-center">
                              <ShoppingBag className="h-6 w-6 text-text-muted opacity-40" />
                            </div>
                          )}
                        </div>
                        <div className="p-1.5">
                          <p className="text-[10px] font-medium truncate text-text">{product.name}</p>
                          <p className="text-[9px] text-text-muted">${product.price?.toFixed(2)}</p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Selected product preview */}
            {selectedProduct && (
              <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 flex gap-3">
                {(selectedProduct.cover_image_url || selectedProduct.cover_image_key) && (
                  <img
                    src={selectedProduct.cover_image_url || cdnUrl(selectedProduct.cover_image_key)}
                    alt={selectedProduct.name}
                    className="h-16 w-16 rounded object-cover border border-border shrink-0"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate text-text">{selectedProduct.name}</p>
                  <p className="text-xs text-text-muted">${selectedProduct.price?.toFixed(2)}</p>
                  <p className="text-[10px] text-accent mt-0.5">Selected for try-on</p>
                </div>
              </div>
            )}

            {/* Auto-generated name */}
            <div>
              <label className="text-sm text-text-muted mb-1 block">Look name</label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Try on: Blue Dress"
                maxLength={100}
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-4 border-t border-border mt-4">
            <Button variant="ghost" onClick={handleClose}>Cancel</Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={!selectedProduct || createMutation.isPending || !hasFrontBodyMotion}
            >
              {createMutation.isPending ? "Starting..." : "Generate Try-On"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="max-w-2xl bg-black text-text border-border">
        <DialogHeader>
          <DialogTitle>
            {lookType === "body_motion" ? "Add body motion pose" : "Add a new look"}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {/* Background presets — only for background looks */}
          {lookType === "background" && (
            <div>
              <label className="text-sm text-text-muted mb-2 block">Quick presets</label>
              <div className="flex flex-wrap gap-2">
                {PRESET_BACKGROUNDS.map((preset) => (
                  <button
                    key={preset.label}
                    onClick={() => handleChipClick(preset)}
                    className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                      selectedChip === preset.label
                        ? "bg-accent text-white border-accent"
                        : "bg-surface text-text-dim border-border hover:border-accent"
                    }`}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Scene properties — environment + mic visibility, decided now
              instead of only per-block afterward. Together they pick the
              voice filter chain (see services/mic_presets.py) that this
              scene's blocks use by default. */}
          {lookType === "background" && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm text-text-muted mb-1 block">Environment</label>
                <select
                  value={environment}
                  onChange={(e) => setEnvironment(e.target.value)}
                  className="w-full text-sm bg-surface text-text border border-border rounded p-2"
                  data-testid="scene-environment-select"
                >
                  {ENVIRONMENT_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-sm text-text-muted mb-1 block">Microphone</label>
                <button
                  type="button"
                  onClick={() => setMicVisible((v) => !v)}
                  className={`w-full text-sm border rounded p-2 transition-colors ${
                    micVisible
                      ? "bg-accent text-white border-accent"
                      : "bg-surface text-text-dim border-border hover:border-accent"
                  }`}
                  data-testid="scene-mic-visible-toggle"
                >
                  {micVisible ? "Clip mic visible" : "No mic (natural)"}
                </button>
              </div>
            </div>
          )}

          {/* Pose angle dropdown — only for body_motion */}
          {lookType === "body_motion" && (
            <div>
              <label className="text-sm text-text-muted mb-1 block">Pose angle</label>
              <select
                value={poseAngle}
                onChange={(e) => {
                  const prevOption = POSE_OPTIONS.find((p) => p.value === poseAngle);
                  setPoseAngle(e.target.value);
                  const option = POSE_OPTIONS.find((p) => p.value === e.target.value);
                  if (option) {
                    setName((prev) => (!prev.trim() || prev === prevOption?.label ? option.label : prev));
                  }
                }}
                className="w-full text-sm bg-surface text-text border border-border rounded p-2"
              >
                {POSE_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label className="text-sm text-text-muted mb-1 block">Look name</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={lookType === "body_motion" ? "e.g., Front Pose, 3/4 Left" : "e.g., Office, Studio, Beach"}
              maxLength={100}
            />
          </div>

          {lookType === "background" && (
            <div>
              <label className="text-sm text-text-muted mb-1 block">Description (what should change)</label>
              <textarea
                value={prompt}
                onChange={(e) => { setPrompt(e.target.value); setSelectedChip(null); }}
                placeholder="Describe the look you want. Same face is always preserved. You can change scene, clothing, accessories, etc."
                className="w-full h-24 text-sm bg-surface text-text border border-border rounded p-2 resize-none"
                maxLength={500}
              />
              <div className="text-xs text-text-muted mt-1">
                {prompt.length}/500
              </div>
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 pt-4 border-t border-border">
          <Button variant="ghost" onClick={handleClose}>Cancel</Button>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={
              (lookType === "background" && !prompt.trim()) ||
              createMutation.isPending
            }
          >
            {createMutation.isPending ? "Starting..." : "Generate Look"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
