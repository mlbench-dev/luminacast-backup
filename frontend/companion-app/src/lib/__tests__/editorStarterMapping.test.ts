import { describe, it, expect } from "vitest";
import {
  castToEditorStarterTimeline,
  editorStarterToLuminacastSnapshot,
  computeBlockRegions,
  type LuminacastSnapshot,
} from "../editorStarterMapping";
import type { Cast } from "../types";
import { CastStatus, BlockType, LayoutMode } from "../types";

/**
 * Port of the backend's extract_bonded_blocks_from_timeline() (Python → TS)
 * from backend/orchestrator/tasks/cast_render.py line 33.
 * Used to verify that our snapshot output is parseable by the renderer.
 */
function extractBondedBlocksFromTimeline(
  timeline: LuminacastSnapshot,
): Array<[any, any]> {
  const elementsById: Record<string, any> = {};

  for (const track of timeline.tracks) {
    for (const el of track.elements) {
      elementsById[el.id] = el;
    }
  }

  const pairs: Array<[any, any]> = [];
  const seenBlocks = new Set<string>();

  for (const el of Object.values(elementsById)) {
    const meta = el.metadata || {};
    if (!meta.bonded || !meta.block_id) continue;
    const blockId = meta.block_id;
    if (seenBlocks.has(blockId)) continue;

    const pairedAudioId = meta.paired_audio_element_id;
    const pairedVideoId = meta.paired_video_element_id;

    if (pairedAudioId) {
      // This is the V1 (snapshot) element
      const a1 = elementsById[pairedAudioId];
      if (a1) {
        pairs.push([el, a1]);
        seenBlocks.add(blockId);
      }
    } else if (pairedVideoId) {
      // This is the A1 (voice) element
      const v1 = elementsById[pairedVideoId];
      if (v1) {
        pairs.push([v1, el]);
        seenBlocks.add(blockId);
      }
    }
  }

  pairs.sort((a, b) => (a[0].s ?? 0) - (b[0].s ?? 0));
  return pairs;
}

// ─── Test fixtures ──────────────────────────────────────

const FIXTURE_CAST: Cast = {
  id: "cast_test_1",
  status: CastStatus.TTS_READY,
  avatar_id: "avatar_1",
  max_duration_minutes: 5,
  loop: false,
  creation_paid: true,
  generation_progress: 100,
  created_at: "2026-04-15T00:00:00Z",
  output_format: "9:16",
  blocks: [
    {
      id: "b_001",
      type: BlockType.HOOK,
      position: 0,
      layout_mode: LayoutMode.FULL_AVATAR,
      auto_basket_enabled: false,
      auto_basket_timeout: 0,
      variant_count: 1,
      variants: [
        {
          id: "v_001",
          variant_label: "default",
          status: "ready",
          audio_key: "casts/cast_test_1/audio/b_001.wav",
          tts_duration_seconds: 4.5,
        },
      ],
    },
    {
      id: "b_002",
      type: BlockType.HOOK,
      position: 1,
      layout_mode: LayoutMode.FULL_AVATAR,
      auto_basket_enabled: false,
      auto_basket_timeout: 0,
      variant_count: 1,
      variants: [
        {
          id: "v_002",
          variant_label: "default",
          status: "ready",
          audio_key: "casts/cast_test_1/audio/b_002.wav",
          tts_duration_seconds: 3.2,
        },
      ],
    },
    {
      id: "b_003",
      type: BlockType.HOOK,
      position: 2,
      layout_mode: LayoutMode.FULL_AVATAR,
      auto_basket_enabled: false,
      auto_basket_timeout: 0,
      variant_count: 1,
      variants: [
        {
          id: "v_003",
          variant_label: "default",
          status: "ready",
          audio_key: "casts/cast_test_1/audio/b_003.wav",
          tts_duration_seconds: 5.0,
        },
      ],
    },
  ],
};

const AVATAR_FACE_KEY = "avatars/avatar_1/face.png";
const CDN_BASE = "https://media.luminacast.com";

// ─── Tests ──────────────────────────────────────────────

describe("castToEditorStarterTimeline", () => {
  it("creates correct number of items and tracks", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    // 2 tracks: video + audio
    expect(state.tracks).toHaveLength(2);

    // 3 video/image items + 3 audio items = 6 total
    const itemIds = Object.keys(state.items);
    expect(itemIds).toHaveLength(6);

    // Video track has 3 items
    expect(state.tracks[0].items).toHaveLength(3);
    // Audio track has 3 items
    expect(state.tracks[1].items).toHaveLength(3);
  });

  it("stacks a talking-head PIP avatar ABOVE its own b-roll background", () => {
    // Regression: a pip_talking_head block with b-roll behind it put the
    // avatar on track-video (under track-broll), so the b-roll background
    // painted over the PIP and the head disappeared.
    const castWithPip: Cast = {
      ...FIXTURE_CAST,
      blocks: [
        {
          ...FIXTURE_CAST.blocks![0],
          // b-roll behind a corner talking-head window
          category: "pip_talking_head",
          parallel_media: [
            { kind: "video", url: "https://x/broll.mp4", start_offset_s: 0, duration_s: 3 },
          ],
        } as any,
        FIXTURE_CAST.blocks![1],
        FIXTURE_CAST.blocks![2],
      ],
    };

    const { state } = castToEditorStarterTimeline(castWithPip, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const ids = state.tracks.map((t) => t.id);
    const pipIdx = ids.indexOf("track-pip-avatar");
    const brollIdx = ids.indexOf("track-broll");
    const videoIdx = ids.indexOf("track-video");

    expect(pipIdx).toBeGreaterThanOrEqual(0);
    expect(brollIdx).toBeGreaterThanOrEqual(0);
    // Layers paints tracks in REVERSED array order, so a lower index paints
    // last = on top. The PIP avatar must come before the b-roll.
    expect(pipIdx).toBeLessThan(brollIdx);

    // The PIP block's avatar item lives on the pip track, not track-video.
    const pipTrack = state.tracks[pipIdx];
    const videoTrack = state.tracks[videoIdx];
    expect(pipTrack.items).toContain("v1_b_001");
    expect(videoTrack.items).not.toContain("v1_b_001");
    // A non-PIP block's avatar stays on track-video.
    expect(videoTrack.items).toContain("v1_b_002");
    expect(pipTrack.items).not.toContain("v1_b_002");
  });

  it("keeps a full-frame cast on just video + audio tracks (no pip track)", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });
    expect(state.tracks.map((t) => t.id)).not.toContain("track-pip-avatar");
  });

  it("sets correct timing in frames (fps=30)", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
      fps: 30,
    });

    // Block 0: 0–4.5s → 0–135 frames
    const v1 = state.items["v1_b_001"];
    expect(v1.from).toBe(0);
    expect(v1.durationInFrames).toBe(135);

    // Block 1: 4.5–7.7s → 135–231 frames
    const v2 = state.items["v1_b_002"];
    expect(v2.from).toBe(135);
    expect(v2.durationInFrames).toBe(96);

    // Block 2: 7.7–12.7s → 231–381 frames
    const v3 = state.items["v1_b_003"];
    expect(v3.from).toBe(231);
    expect(v3.durationInFrames).toBe(150);
  });

  it("preserves block_id metadata on all items", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    for (const item of Object.values(state.items)) {
      expect(item.metadata).toBeDefined();
      expect(item.metadata!.block_id).toBeTruthy();
      expect(item.metadata!.bonded).toBe(true);
    }
  });

  it("creates bonded pairs with correct cross-references", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    // For each block, V1 points to A1 and vice versa
    for (const block of FIXTURE_CAST.blocks!) {
      const v1 = state.items[`v1_${block.id}`];
      const a1 = state.items[`a1_${block.id}`];

      expect(v1).toBeDefined();
      expect(a1).toBeDefined();
      expect(v1.metadata!.paired_audio_element_id).toBe(`a1_${block.id}`);
      expect(a1.metadata!.paired_video_element_id).toBe(`v1_${block.id}`);
    }
  });

  it("creates image items when no rendered video exists", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    // No variant has video_key/stream_url/clip_url, so all V1 items are images
    const v1 = state.items["v1_b_001"];
    expect(v1.type).toBe("image");
  });

  it("creates video items when rendered video exists", () => {
    const castWithVideo: Cast = {
      ...FIXTURE_CAST,
      blocks: [
        {
          ...FIXTURE_CAST.blocks![0],
          variants: [
            {
              ...FIXTURE_CAST.blocks![0].variants![0],
              video_key: "casts/cast_test_1/video/b_001.mp4",
            },
          ],
        },
      ],
    };

    const { state } = castToEditorStarterTimeline(castWithVideo, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const v1 = state.items["v1_b_001"];
    expect(v1.type).toBe("video");
  });

  it("creates assets for each item", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    // 3 image assets (face) + 3 audio assets = 6
    expect(Object.keys(state.assets)).toHaveLength(6);

    // Image asset has the CDN URL
    const faceAsset = state.assets["asset_v1_b_001"];
    expect(faceAsset.type).toBe("image");
    expect(faceAsset.remoteUrl).toBe(`${CDN_BASE}/${AVATAR_FACE_KEY}`);

    // Audio asset has the CDN URL
    const audioAsset = state.assets["asset_a1_b_001"];
    expect(audioAsset.type).toBe("audio");
    expect(audioAsset.remoteUrl).toBe(
      `${CDN_BASE}/casts/cast_test_1/audio/b_001.wav`,
    );
  });

  it("computes correct blockRegions", () => {
    const { blockRegions } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    expect(blockRegions).toHaveLength(3);
    expect(blockRegions[0]).toEqual({
      block_id: "b_001",
      block_position: 0,
      variant_id: "v_001",
      start_s: 0,
      end_s: 4.5,
    });
    expect(blockRegions[1]).toEqual({
      block_id: "b_002",
      block_position: 1,
      variant_id: "v_002",
      start_s: 4.5,
      end_s: 7.7,
    });
    expect(blockRegions[2]).toEqual({
      block_id: "b_003",
      block_position: 2,
      variant_id: "v_003",
      start_s: 7.7,
      end_s: 12.7,
    });
  });
});

describe("editorStarterToLuminacastSnapshot", () => {
  it("produces renderer-compatible bonded V1/A1 tracks", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);

    expect(snapshot.version).toBe(1);
    expect(snapshot.tracks).toHaveLength(2);
    expect(snapshot.tracks[0].type).toBe("video");
    expect(snapshot.tracks[1].type).toBe("audio");
  });

  it("converts frames to seconds correctly", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
      fps: 30,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);

    const videoEls = snapshot.tracks[0].elements;
    expect(videoEls[0].s).toBe(0);
    expect(videoEls[0].e).toBe(4.5);
    expect(videoEls[1].s).toBe(4.5);
    expect(videoEls[1].e).toBeCloseTo(7.7, 1);
  });

  it("resolves asset URLs into props.src", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);

    // V1 elements have face image URL
    expect(snapshot.tracks[0].elements[0].props.src).toBe(
      `${CDN_BASE}/${AVATAR_FACE_KEY}`,
    );

    // A1 elements have audio URL
    expect(snapshot.tracks[1].elements[0].props.src).toBe(
      `${CDN_BASE}/casts/cast_test_1/audio/b_001.wav`,
    );
  });

  it("preserves metadata through round-trip", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);

    for (const el of snapshot.tracks[0].elements) {
      expect(el.metadata).toBeDefined();
      expect(el.metadata!.block_id).toBeTruthy();
      expect(el.metadata!.bonded).toBe(true);
      expect(el.metadata!.paired_audio_element_id).toBeTruthy();
    }

    for (const el of snapshot.tracks[1].elements) {
      expect(el.metadata).toBeDefined();
      expect(el.metadata!.block_id).toBeTruthy();
      expect(el.metadata!.bonded).toBe(true);
      expect(el.metadata!.paired_video_element_id).toBeTruthy();
    }
  });
});

describe("round-trip: cast → editor state → luminacast snapshot", () => {
  it("preserves all block_ids through round-trip", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);

    const blockIds = new Set<string>();
    for (const track of snapshot.tracks) {
      for (const el of track.elements) {
        if (el.metadata?.block_id) {
          blockIds.add(el.metadata.block_id);
        }
      }
    }

    // All 3 block IDs present
    expect(blockIds).toEqual(new Set(["b_001", "b_002", "b_003"]));
  });

  it("bonded pairs are extractable by the renderer logic", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);
    const pairs = extractBondedBlocksFromTimeline(snapshot);

    // 3 bonded pairs (one per block)
    expect(pairs).toHaveLength(3);

    // Each pair has matching block_id between V1 and A1
    for (const [v1, a1] of pairs) {
      expect(v1.metadata.block_id).toBeTruthy();
      expect(v1.metadata.block_id).toBe(a1.metadata.block_id);
    }

    // Pairs are sorted by start time
    expect(pairs[0][0].s).toBe(0);
    expect(pairs[1][0].s).toBe(4.5);
    expect(pairs[2][0].s).toBeCloseTo(7.7, 1);
  });

  it("bonded pairs have correct src URLs for face and audio", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);
    const pairs = extractBondedBlocksFromTimeline(snapshot);

    for (const [v1, a1] of pairs) {
      // V1 has avatar face image
      expect(v1.props.src).toContain("face.png");
      // A1 has audio
      expect(a1.props.src).toContain(".wav");
    }
  });

  it("bonded pairs have matching start/end times", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const snapshot = editorStarterToLuminacastSnapshot(state);
    const pairs = extractBondedBlocksFromTimeline(snapshot);

    for (const [v1, a1] of pairs) {
      expect(v1.s).toBe(a1.s);
      expect(v1.e).toBe(a1.e);
    }
  });
});

describe("computeBlockRegions", () => {
  it("computes regions from editor state", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const regions = computeBlockRegions(state);

    expect(regions).toHaveLength(3);
    expect(regions[0].block_id).toBe("b_001");
    expect(regions[0].start_s).toBe(0);
    expect(regions[0].end_s).toBe(4.5);
    expect(regions[1].block_id).toBe("b_002");
    expect(regions[2].block_id).toBe("b_003");
  });
});

describe("edge cases", () => {
  it("handles cast with no blocks", () => {
    const emptyCast: Cast = {
      ...FIXTURE_CAST,
      blocks: [],
    };

    const { state, blockRegions } = castToEditorStarterTimeline(emptyCast, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    expect(Object.keys(state.items)).toHaveLength(0);
    expect(blockRegions).toHaveLength(0);
    expect(state.tracks).toHaveLength(2); // tracks exist but are empty

    const snapshot = editorStarterToLuminacastSnapshot(state);
    expect(snapshot.tracks[0].elements).toHaveLength(0);
    expect(snapshot.tracks[1].elements).toHaveLength(0);
  });

  it("handles block with no audio_key (uses 5s default duration)", () => {
    const castNoAudio: Cast = {
      ...FIXTURE_CAST,
      blocks: [
        {
          id: "b_noaudio",
          type: BlockType.HOOK,
          position: 0,
          layout_mode: LayoutMode.FULL_AVATAR,
          auto_basket_enabled: false,
          auto_basket_timeout: 0,
          variant_count: 1,
          variants: [
            {
              id: "v_noaudio",
              variant_label: "default",
              status: "ready",
              // no audio_key, no tts_duration_seconds
            },
          ],
        },
      ],
    };

    const { state, blockRegions } = castToEditorStarterTimeline(castNoAudio, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    // Duration defaults to 5s
    expect(blockRegions[0].end_s).toBe(5);

    // Image item created (face), but no audio item (no src)
    const v1 = state.items["v1_b_noaudio"];
    expect(v1).toBeDefined();
    expect(v1.durationInFrames).toBe(150); // 5s * 30fps
  });

  it("handles cast with no avatar face key", () => {
    const { state } = castToEditorStarterTimeline(FIXTURE_CAST, {
      // no avatarFaceKey
    });

    // No V1 items since there's no face and no rendered video
    expect(state.tracks[0].items).toHaveLength(0);
    // Audio items still created
    expect(state.tracks[1].items).toHaveLength(3);
  });

  // Regression 4 follow-up: [sfx:NAME] markers were correctly extracted and
  // time-aligned into variant.sfx_timings server-side, but nothing in this
  // editor path — the one every real cast actually goes through — ever
  // turned that into a playable timeline element, so the sound never
  // reached the render. See services/sfx_library.py /
  // tests/unit/test_sfx_resolver.py for the backend half of this contract.
  it("resolves sfx_timings into a real audio element on its own track", () => {
    const baseBlock = FIXTURE_CAST.blocks![0];
    const castWithSfx: Cast = {
      ...FIXTURE_CAST,
      blocks: [
        {
          ...baseBlock,
          variants: [
            {
              ...baseBlock.variants![0],
              sfx_timings: [{ name: "record_scratch", start_s: 0.2 }],
            },
          ],
        },
      ],
    };

    const { state } = castToEditorStarterTimeline(castWithSfx, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const sfxTrack = state.tracks.find((t) => t.id === "track-sfx");
    expect(sfxTrack).toBeDefined();
    expect(sfxTrack!.items).toHaveLength(1);

    const sfxItem = state.items[sfxTrack!.items[0]] as any;
    expect(sfxItem.type).toBe("audio");
    expect(sfxItem.metadata.kind).toBe("sfx");
    expect(sfxItem.metadata.name).toBe("record_scratch");
    expect(sfxItem.from).toBe(6); // 0.2s * 30fps

    const sfxAsset = state.assets[sfxItem.assetId] as any;
    expect(sfxAsset.remoteUrl).toBe(`${CDN_BASE}/sfx/record_scratch.wav`);
  });

  it("skips an unknown sfx name without throwing", () => {
    const baseBlock = FIXTURE_CAST.blocks![0];
    const castWithBadSfx: Cast = {
      ...FIXTURE_CAST,
      blocks: [
        {
          ...baseBlock,
          variants: [
            {
              ...baseBlock.variants![0],
              sfx_timings: [{ name: "not_a_real_sound", start_s: 0 }],
            },
          ],
        },
      ],
    };

    const { state } = castToEditorStarterTimeline(castWithBadSfx, {
      avatarFaceKey: AVATAR_FACE_KEY,
    });

    const sfxTrack = state.tracks.find((t) => t.id === "track-sfx");
    expect(sfxTrack).toBeUndefined();
  });
});
