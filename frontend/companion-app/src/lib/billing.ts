export const PRICING = {
  AVATAR_SETUP_CENTS: 999,
  CAST_CREATION_CENTS: 1499,
  STREAMING_PER_MINUTE_CENTS: 3,
  CLIP_REGENERATION_CENTS: 99,
} as const;

export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export function formatDollars(dollars: number): string {
  return `$${dollars.toFixed(2)}`;
}

export function calculateStreamingCost(minutes: number): number {
  return Math.ceil(minutes) * PRICING.STREAMING_PER_MINUTE_CENTS;
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

