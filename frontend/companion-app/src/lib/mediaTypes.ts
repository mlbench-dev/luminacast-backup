export type MediaSource = "library" | "stock" | "generated";
export type MediaItemType = "video" | "photo";

export interface MediaItem {
  id: string | number;
  source: MediaSource;
  type: MediaItemType;
  thumbnail: string;
  url?: string;
  src?: string;
  duration?: number;
  name?: string;
  photographer?: string;
  photographer_url?: string;
  pexels_url?: string;
  width?: number;
  height?: number;
  file_size_bytes?: number;
}
