import { useCallback, useEffect, useRef, useState } from "react";
import ReactCrop, { type Crop, type PixelCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { IMAGE_SIZE_BY_LAYOUT } from "@/lib/layoutOptions";

// Matches the backend's avatar animation canvas (services/face_extraction.py
// detect_and_frame_face target_w/target_h, sized per the avatar's picked
// layout via IMAGE_SIZE_BY_LAYOUT). The crop selection itself is NOT locked
// to this ratio — locking it forced a narrow vertical strip out of any
// landscape photo, cutting off arms/shoulders that were wider than a 9:16
// strip could ever contain. Instead the user can select any rectangle (up
// to the whole photo) and it gets fit into this canvas afterward, padding
// the leftover space rather than cropping anything further.
const DEFAULT_TARGET = IMAGE_SIZE_BY_LAYOUT["9:16"];
// Floor on the crop selection size (in on-screen pixels) so it can't be
// resized down to something unusably tiny.
const MIN_CROP_SIZE = 40;

// One shared 1x1 sampling canvas, reused per call — cheap and avoids
// allocating a new canvas per corner.
const _sampleCanvas = document.createElement("canvas");
_sampleCanvas.width = 1;
_sampleCanvas.height = 1;
const _sampleCtx = _sampleCanvas.getContext("2d", { willReadFrequently: true });

function sampleEdgeColor(image: HTMLImageElement, sx: number, sy: number, sw: number, sh: number): string {
  if (!_sampleCtx) return "#ffffff";
  const corners: Array<[number, number]> = [
    [sx + 1, sy + 1],
    [sx + sw - 1, sy + 1],
    [sx + 1, sy + sh - 1],
    [sx + sw - 1, sy + sh - 1],
  ];
  let r = 0, g = 0, b = 0;
  for (const [cx, cy] of corners) {
    _sampleCtx.clearRect(0, 0, 1, 1);
    _sampleCtx.drawImage(image, cx, cy, 1, 1, 0, 0, 1, 1);
    const [pr, pg, pb] = _sampleCtx.getImageData(0, 0, 1, 1).data;
    r += pr; g += pg; b += pb;
  }
  return `rgb(${Math.round(r / 4)}, ${Math.round(g / 4)}, ${Math.round(b / 4)})`;
}

async function buildFramedImage(
  image: HTMLImageElement,
  crop: PixelCrop,
  targetW: number,
  targetH: number,
): Promise<Blob> {
  // react-image-crop reports the crop in on-screen (rendered) pixels — scale
  // up to the source file's actual resolution before drawing from it.
  const scaleX = image.naturalWidth / image.width;
  const scaleY = image.naturalHeight / image.height;
  const sx = crop.x * scaleX;
  const sy = crop.y * scaleY;
  const sw = crop.width * scaleX;
  const sh = crop.height * scaleY;

  const canvas = document.createElement("canvas");
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas not supported");

  // Fill behind any leftover padding with a plain color sampled from the
  // selection's own corners (most avatar photos are shot against a flat
  // studio background) — this blends the padding in instead of showing an
  // obvious blurred/ghosted duplicate of the photo.
  ctx.fillStyle = sampleEdgeColor(image, sx, sy, sw, sh);
  ctx.fillRect(0, 0, targetW, targetH);

  // The actual selection on top, scaled to fit WITHOUT cropping or
  // stretching — this is what guarantees nothing the user selected gets
  // cut off, regardless of the shape they drew.
  const containScale = Math.min(targetW / sw, targetH / sh);
  const fgW = sw * containScale;
  const fgH = sh * containScale;
  ctx.drawImage(image, sx, sy, sw, sh, (targetW - fgW) / 2, (targetH - fgH) / 2, fgW, fgH);

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Crop failed"));
    }, "image/jpeg", 0.95);
  });
}

export function ImageCropModal({
  file,
  targetLayout,
  onCancel,
  onConfirm,
}: {
  file: File;
  /** "9:16" | "16:9" | "1:1" | "4:5" — defaults to 9:16 if omitted/unknown. */
  targetLayout?: string;
  onCancel: () => void;
  onConfirm: (croppedFile: File) => void;
}) {
  const target = (targetLayout && IMAGE_SIZE_BY_LAYOUT[targetLayout]) || DEFAULT_TARGET;
  const [imageUrl] = useState(() => URL.createObjectURL(file));
  const [crop, setCrop] = useState<Crop>();
  const [completedCrop, setCompletedCrop] = useState<PixelCrop>();
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [processing, setProcessing] = useState(false);

  useEffect(() => () => URL.revokeObjectURL(imageUrl), [imageUrl]);

  const onImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const { width, height } = e.currentTarget;
    // Default to selecting the whole photo — nothing is cut off unless the
    // user deliberately shrinks the selection themselves.
    const initial: Crop = { unit: "%", x: 0, y: 0, width: 100, height: 100 };
    setCrop(initial);
    setCompletedCrop({
      unit: "px", x: 0, y: 0, width, height,
    });
  }, []);

  const handleConfirm = useCallback(async () => {
    if (!completedCrop || !imgRef.current) return;
    setProcessing(true);
    try {
      const blob = await buildFramedImage(imgRef.current, completedCrop, target.width, target.height);
      const baseName = file.name.replace(/\.[^./\\]+$/, "");
      onConfirm(new File([blob], `${baseName}-cropped.jpg`, { type: "image/jpeg" }));
    } catch {
      setProcessing(false);
    }
  }, [completedCrop, file.name, onConfirm, target.width, target.height]);

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Position your photo</DialogTitle>
          <DialogDescription>
            Drag the corners to resize the frame so it includes everything you want visible — any shape is fine, we'll fit it to the avatar canvas without cutting anything off. Drag inside the frame to move it.
          </DialogDescription>
        </DialogHeader>

        <div
          className="max-h-[60vh] overflow-auto rounded-lg bg-black flex justify-center [&_.ReactCrop__crop-selection]:drop-shadow-[0_0_0_1px_rgba(0,0,0,0.85)]"
          style={{ "--rc-border-color": "#a78bfa", "--rc-focus-color": "#a78bfa" } as React.CSSProperties}
        >
          <ReactCrop
            crop={crop}
            onChange={(_, percentCrop) => setCrop(percentCrop)}
            onComplete={(c) => setCompletedCrop(c)}
            minWidth={MIN_CROP_SIZE}
            minHeight={MIN_CROP_SIZE}
            keepSelection
          >
            {/* eslint-disable-next-line jsx-a11y/alt-text */}
            <img
              ref={imgRef}
              src={imageUrl}
              onLoad={onImageLoad}
              alt="Crop preview"
              className="max-h-[60vh] w-auto"
            />
          </ReactCrop>
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={processing}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={processing || !completedCrop}>
            {processing ? "Processing…" : "Use this crop"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
