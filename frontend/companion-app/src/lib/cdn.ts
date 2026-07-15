export const CDN_URL = import.meta.env.VITE_CDN_URL || "https://media.luminacast.com";

export function cdnUrl(key: string | null | undefined): string {
  if (!key) return "";
  if (key.startsWith("http://") || key.startsWith("https://") || key.startsWith("/")) return key;
  return `${CDN_URL}/${key.replace(/^\/+/, "")}`;
}
