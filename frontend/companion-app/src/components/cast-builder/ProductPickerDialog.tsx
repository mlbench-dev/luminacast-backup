import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { productsApi } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import type { ProductWithAssets, ProductAsset, ProductVariant } from "@/lib/types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Loader2, ArrowLeft, Package, Image as ImageIcon } from "lucide-react";
import { ProductMediaGallery } from "@/components/cast-builder/ProductMediaGallery";

export interface ProductPickResult {
  product: ProductWithAssets;
  variant_id?: string;
  asset_url: string;
  asset_id: string;
}

interface ProductPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (result: ProductPickResult) => void;
}

type Step = "product" | "variant" | "photo";

export function ProductPickerDialog({
  open,
  onOpenChange,
  onPick,
}: ProductPickerDialogProps) {
  const [step, setStep] = useState<Step>("product");
  const [products, setProducts] = useState<ProductWithAssets[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<ProductWithAssets | null>(null);
  const [selectedVariantId, setSelectedVariantId] = useState<string | undefined>();

  useEffect(() => {
    if (open) {
      setStep("product");
      setSelectedProduct(null);
      setSelectedVariantId(undefined);
      setLoading(true);
      productsApi
        .list({ per_page: 50 })
        .then((res) => setProducts(res.products))
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [open]);

  const imageAssets = (selectedProduct?.assets || []).filter(
    (a) => a.media_type === "image",
  );

  const handleProductSelect = (product: ProductWithAssets) => {
    setSelectedProduct(product);
    const variants = product.variants || [];
    if (variants.length > 1) {
      setStep("variant");
    } else {
      setStep("photo");
    }
  };

  const handleVariantSelect = (variantId: string) => {
    setSelectedVariantId(variantId);
    setStep("photo");
  };

  const handlePhotoSelect = (asset: ProductAsset) => {
    if (!selectedProduct) return;
    onPick({
      product: selectedProduct,
      variant_id: selectedVariantId,
      asset_url: asset.r2_url || cdnUrl(asset.r2_key),
      asset_id: asset.id,
    });
    onOpenChange(false);
  };

  const handleBack = () => {
    if (step === "photo") {
      const variants = selectedProduct?.variants || [];
      setStep(variants.length > 1 ? "variant" : "product");
    } else if (step === "variant") {
      setStep("product");
      setSelectedProduct(null);
    }
  };

  const stepTitle =
    step === "product"
      ? "Select a Product"
      : step === "variant"
        ? "Select a Variant"
        : "Select a Photo";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2">
            {step !== "product" && (
              <Button variant="ghost" size="sm" onClick={handleBack} className="text-white/60 hover:text-white p-1">
                <ArrowLeft className="w-4 h-4" />
              </Button>
            )}
            <DialogTitle>{stepTitle}</DialogTitle>
          </div>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-white/50" />
          </div>
        ) : step === "product" ? (
          <div className="grid grid-cols-3 gap-3">
            {products.length === 0 && (
              <div className="col-span-3 text-center text-white/40 py-8">
                <Package className="w-8 h-8 mx-auto mb-2 opacity-40" />
                No products yet
              </div>
            )}
            {products.map((p) => (
              <ProductPickerCard
                key={p.id}
                product={p}
                onSelect={handleProductSelect}
              />
            ))}
          </div>
        ) : step === "variant" ? (
          <div className="grid grid-cols-2 gap-3">
            {(selectedProduct?.variants || []).map((v) => (
              <button
                key={v.variantId || v.name}
                onClick={() => handleVariantSelect(v.variantId || v.name)}
                className="flex flex-col items-center gap-2 p-4 rounded-lg border border-white/10 hover:border-purple-500/50 hover:bg-white/5 transition-colors"
              >
                {v.imageUrl ? (
                  <img
                    src={v.imageUrl}
                    alt={v.name}
                    className="w-20 h-20 object-cover rounded-md bg-white/5"
                  />
                ) : (
                  <div className="w-20 h-20 rounded-md bg-white/5 flex items-center justify-center">
                    <Package className="w-6 h-6 text-white/20" />
                  </div>
                )}
                <span className="text-sm text-white/80">{v.name}</span>
                {v.price && (
                  <span className="text-xs text-white/50">${v.price}</span>
                )}
              </button>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            {imageAssets.length === 0 && (
              <div className="col-span-3 text-center text-white/40 py-8">
                <ImageIcon className="w-8 h-8 mx-auto mb-2 opacity-40" />
                No images available
              </div>
            )}
            {imageAssets.map((asset) => (
              <button
                key={asset.id}
                onClick={() => handlePhotoSelect(asset)}
                className="rounded-lg border border-white/10 hover:border-purple-500/50 overflow-hidden transition-colors"
              >
                <img
                  src={asset.r2_url || cdnUrl(asset.r2_key)}
                  alt={asset.asset_type}
                  className="w-full aspect-square object-cover"
                />
              </button>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ProductPickerCard({
  product,
  onSelect,
}: {
  product: ProductWithAssets;
  onSelect: (p: ProductWithAssets) => void;
}) {
  // Skip the per-card asset fetch when the product is known to have at
  // most one asset — the cover already covers that case and we'd waste a
  // round-trip per visible card on dialog open.
  const shouldFetch = (product.asset_count ?? 0) > 1;
  const { data: assets } = useQuery({
    queryKey: ["product-assets", product.id],
    queryFn: () => productsApi.getAssets(product.id),
    enabled: shouldFetch,
    staleTime: 60_000,
  });

  return (
    <button
      onClick={() => onSelect(product)}
      className="flex flex-col items-center gap-2 p-3 rounded-lg border border-white/10 hover:border-purple-500/50 hover:bg-white/5 transition-colors text-left"
    >
      <ProductMediaGallery
        product={product}
        assets={shouldFetch ? assets : []}
        className="rounded-md"
      />
      <span className="text-sm text-white/80 truncate w-full text-center">
        {product.name}
      </span>
    </button>
  );
}
