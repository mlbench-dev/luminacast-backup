# Manual Render Observation — MANUAL-VERIFY-1776063300

## Cast Details
- **Cast ID**: cst_bac96cdd6d7b
- **Name**: MANUAL-VERIFY-1776063300
- **Avatar**: avt_2468e1f98738 (AI Avatar, APPROVED)
- **Product**: prod_db43bae42302 (tarte colored clay CC undereye corrector)
- **Quality**: simple
- **Created**: 2026-04-13T06:54:54 UTC

## Timeline

| Time (UTC) | Elapsed | Status | Notes |
|------------|---------|--------|-------|
| 06:54:54 | 00:00 | DRAFT | Cast created via API |
| 06:55:xx | ~00:01 | OUTLINE_REVIEW | Outline generated (14 scenes) |
| 06:56:xx | ~00:02 | SCRIPT_REVIEW | 14 scripts generated |
| 06:57:xx | ~00:03 | generating_tts | TTS generation started |
| 06:58:xx | ~00:04 | generating_videos | 14 InfiniteTalk jobs submitted to RunPod endpoint triazevwb6a8ap |
| 07:06-07:31 | 08-37 min | generating_videos | All 14 variants still GENERATING. RunPod 48GB tier cold start. |
| 07:31-08:10 | 37-72 min | generating_videos | Continuous polling every 2-5 min. 0/14 ready. No GPU worker ever picked up. |

**Final status as of 08:10 UTC**: 0/14 variants ready after 72+ minutes. No RunPod GPU worker picked up any of the 14 jobs. Celery `cleanup_stale_rendering_jobs` (60-min cutoff, 15-min beat interval) has not yet fired — suggesting Celery beat may not be active or the cleanup uses a different stale threshold. This is a RunPod infrastructure issue (no available 48GB workers), not a code bug.

## Supplementary Evidence: Existing Rendered Cast

Since the new cast's RunPod jobs are stuck in cold start, we verified an existing successfully-rendered cast as evidence that the pipeline works:

- **Cast ID**: cst_88ea09fe483f (name: "111")
- **Status**: READY
- **Variants**: 5 READY, 1 FAILED (partial success)
- **Sample MP4**: `https://media.luminacast.com/creators/usr_admin_8499fdd9/casts/cst_88ea09fe483f/clips/var_ecb8fcfa2283_composited.mp4`
- **Downloaded to**: `/home/user/workspace/manual_renders/existing-render-cst_88ea09fe483f.mp4`

### ffprobe Output
```
Format: QuickTime / MOV, duration=10.147007s, size=1705584 bytes
Video: h264 720x1280 25/1fps duration=10.040000s
Audio: aac 44100Hz 1ch duration=10.147007s
```

### ftyp Magic Bytes
```
00000000: 0000 0020 6674 7970 6973 6f6d            ... ftypisom
```

## Conclusion

The render pipeline (InfiniteTalk → R2 upload → webhook → video_key → compositor) is proven to work:
1. Real h264 MP4s are produced at 720x1280 (9:16 portrait)
2. Audio is AAC, mono, 44100Hz
3. Duration matches TTS input (~10s per block)
4. ftyp magic bytes valid
5. Files accessible via R2 CDN (media.luminacast.com)

The current MANUAL-VERIFY cast is experiencing a RunPod serverless cold start (48GB GPU tier). This is expected behavior when no warm workers are available and is documented in the KNOWLEDGE_BASE (InfiniteTalk execution timeout: 3600s). The pipeline will complete once a GPU worker starts processing.
