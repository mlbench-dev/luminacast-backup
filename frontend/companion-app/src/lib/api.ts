/**
 * Barrel file — every domain API module lives under src/api/<name>Api.ts.
 * This file exists so existing `import { xApi } from "@/lib/api"` call
 * sites across the app keep working untouched; new code can import
 * directly from "@/api/<name>Api" instead if preferred.
 */
import { QueryClient } from "@tanstack/react-query";

import { api, extractErrorMessage, setAuthToken } from "./apiClient";
export { api, extractErrorMessage, setAuthToken };

// React Query client
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

// ── Domain API modules ──

import { authApi } from "@/api/authApi";
export { authApi };
import { teamsApi } from "@/api/teamsApi";
export { teamsApi };
import { userApi } from "@/api/userApi";
export { userApi };
import { castsApi } from "@/api/castsApi";
export { castsApi };
import { productsApi } from "@/api/productsApi";
export { productsApi };
import { channelsApi } from "@/api/channelsApi";
export { channelsApi };
import { streamApi } from "@/api/streamApi";
export { streamApi };
import { avatarApi } from "@/api/avatarApi";
export { avatarApi };
import { discoverApi } from "@/api/discoverApi";
export { discoverApi };
import { tiktokShopApi } from "@/api/tiktokShopApi";
export { tiktokShopApi };
import { layoutsApi } from "@/api/layoutsApi";
export { layoutsApi };
import { scriptApi } from "@/api/scriptApi";
export { scriptApi };
import { analyticsApi } from "@/api/analyticsApi";
export { analyticsApi };
import { chatApi } from "@/api/chatApi";
export { chatApi };
import { aceStepApi } from "@/api/aceStepApi";
export { aceStepApi };
import { musicApi } from "@/api/musicApi";
export { musicApi };
import { adminApi } from "@/api/adminApi";
export { adminApi };
import { userVideosApi } from "@/api/userVideosApi";
export { userVideosApi };
import { userPhotosApi } from "@/api/userPhotosApi";
export { userPhotosApi };
import { generatedVideosApi } from "@/api/generatedVideosApi";
export { generatedVideosApi };
import { generatedPhotosApi } from "@/api/generatedPhotosApi";
export { generatedPhotosApi };
import { avatarLooksApi } from "@/api/avatarLooksApi";
export { avatarLooksApi };
import { voiceCorpusApi } from "@/api/voiceCorpusApi";
export { voiceCorpusApi };
import { liveSessionApi } from "@/api/liveSessionApi";
export { liveSessionApi };
import { goLiveApi } from "@/api/goLiveApi";
export { goLiveApi };
import { socialApi, confirmConnectWithRetry } from "@/api/socialApi";
export { socialApi, confirmConnectWithRetry };
import { historyApi } from "@/api/historyApi";
export { historyApi };
import { stockMediaApi } from "@/api/stockMediaApi";
export { stockMediaApi };
import { billingApi } from "@/api/billingApi";
export { billingApi };

// ── Re-exported types (declared alongside their owning API module) ──

export type { TeamMemberDto, WorkspaceOptionDto } from "@/api/teamsApi";
export type { ReviewQueueCast } from "@/api/castsApi";
export type { NeedsManualEntry, FromUrlResult } from "@/api/productsApi";
export { isNeedsManualEntry } from "@/api/productsApi";
export type { MusicLibraryTrack, MusicSoundCast, MusicTrackItem } from "@/api/aceStepApi";
export type {
  CatalogTrack,
  UploadedTrack,
  SfxItem,
  AIGeneratedTrack,
  LibraryParamOption,
  LibraryParams,
  LibraryTrack,
  LibraryTracksResponse,
} from "@/api/musicApi";
export type { SocialChannel, AvatarConsistencyResponse } from "@/api/socialApi";
export type { HistoryEvent, HistoryResponse } from "@/api/historyApi";
