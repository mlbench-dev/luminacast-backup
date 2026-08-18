import { useCallback, useEffect, useState } from "react";
import Cropper, { type Area } from "react-easy-crop";
import "react-easy-crop/react-easy-crop.css";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

// Matches the backend's avatar animation canvas (services/face_extraction.py
// detect_and_frame_face target_w/target_h) so a manually-cropped photo needs
// little to no further adjustment server-side.
const TARGET_ASPECT = 720 / 1280;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.addEventListener("load", () => resolve(img));
    img.addEventListener("error", () => reject(new Error("Failed to load image")));
    img.src = src;
  });
}

async function getCroppedImageFile(
  imageSrc: string,
  area: Area,
  fileName: string,
): Promise<File> {
  const image = await loadImage(imageSrc);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(area.width));
  canvas.height = Math.max(1, Math.round(area.height));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas not supported");
  ctx.drawImage(
    image,
    area.x, area.y, area.width, area.height,
    0, 0, canvas.width, canvas.height,
  );
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("Crop failed"));
        return;
      }
      resolve(new File([blob], fileName, { type: "image/jpeg" }));
    }, "image/jpeg", 0.95);
  });
}

export function ImageCropModal({
  file,
  onCancel,
  onConfirm,
}: {
  file: File;
  onCancel: () => void;
  onConfirm: (croppedFile: File) => void;
}) {
  const [imageUrl] = useState(() => URL.createObjectURL(file));
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [croppedAreaPixels, setCroppedAreaPixels] = useState<Area | null>(null);
  const [processing, setProcessing] = useState(false);

  useEffect(() => () => URL.revokeObjectURL(imageUrl), [imageUrl]);

  const onCropComplete = useCallback((_area: Area, areaPixels: Area) => {
    setCroppedAreaPixels(areaPixels);
  }, []);

  const handleConfirm = useCallback(async () => {
    if (!croppedAreaPixels) return;
    setProcessing(true);
    try {
      const baseName = file.name.replace(/\.[^./\\]+$/, "");
      const croppedFile = await getCroppedImageFile(imageUrl, croppedAreaPixels, `${baseName}-cropped.jpg`);
      onConfirm(croppedFile);
    } catch {
      setProcessing(false);
    }
  }, [croppedAreaPixels, imageUrl, file.name, onConfirm]);

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Position your photo</DialogTitle>
          <DialogDescription>
            Drag to reposition, use the slider to zoom. Keep your head and shoulders inside the frame — the auto-framing isn't always perfect, so adjust it yourself here if it looks off.
          </DialogDescription>
        </DialogHeader>

        <div className="relative w-full h-80 bg-black rounded-lg overflow-hidden">
          <Cropper
            image={imageUrl}
            crop={crop}
            zoom={zoom}
            aspect={TARGET_ASPECT}
            cropShape="rect"
            showGrid
            onCropChange={setCrop}
            onZoomChange={setZoom}
            onCropComplete={onCropComplete}
          />
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-white/50 shrink-0">Zoom</span>
          <input
            type="range"
            min={1}
            max={3}
            step={0.01}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            className="flex-1 accent-primary"
          />
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={processing}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={processing || !croppedAreaPixels}>
            {processing ? "Processing…" : "Use this crop"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
