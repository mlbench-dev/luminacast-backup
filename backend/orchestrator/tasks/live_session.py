"""Celery task for live session generation loop.

Pipeline per paragraph:
  1. Claude generates a selling script paragraph
  2. Fish Speech TTS on GPU server generates audio
  3. FFmpeg composites audio + footage into an HLS segment
  4. m3u8 playlist is updated (sliding window)
"""
import asyncio
import base64
import logging
import os
import json
from datetime import datetime, timezone

from tasks import celery_app

import sentry_sdk
logger = logging.getLogger(__name__)


def _make_session_factory():
    """Create a fresh async session factory for Celery worker context."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(name="tasks.live_session.run", bind=True, max_retries=0)
def run_live_session_task(self, session_id: str):
    """Entry point - runs the async live loop in a new event loop."""
    logger.info(json.dumps({
        "service": "live_session", "level": "info",
        "message": f"Starting live session task: {session_id}",
        "timestamp": datetime.utcnow().isoformat(),
    }))
    asyncio.run(_run_live_loop(session_id))


async def _run_live_loop(session_id: str):
    """Main live generation loop."""
    from sqlalchemy import select
    from models.live_session import LiveSession
    from models.avatar import Avatar
    from models.product import Product
    from models.voice_corpus import VoiceCorpusEntry

    factory = _make_session_factory()

    async with factory() as db:
        ls = await db.get(LiveSession, session_id)
        if not ls:
            logger.error("Live session %s not found", session_id)
            return

        avatar = await db.get(Avatar, ls.avatar_id)
        if not avatar:
            ls.status = "failed"
            ls.error_message = "Avatar not found"
            await db.commit()
            return

        # Load voice corpus for script generation context
        corpus_entries = (await db.execute(
            select(VoiceCorpusEntry)
            .where(VoiceCorpusEntry.avatar_id == avatar.id)
            .where(VoiceCorpusEntry.status == "ready")
            .limit(10)
        )).scalars().all()
        corpus_context = "\n---\n".join(
            e.transcript[:1000] for e in corpus_entries if e.transcript
        )

        # Load reference audio for TTS from first corpus entry with audio
        reference_audio_b64 = None
        for entry in corpus_entries:
            if entry.audio_r2_key:
                try:
                    reference_audio_b64 = await _download_reference_audio(entry.audio_r2_key)
                    break
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logger.warning("Failed to load reference audio %s: %s", entry.audio_r2_key, e)

        # Load products
        products = []
        for item in (ls.product_queue or []):
            product = await db.get(Product, item["product_id"])
            if product:
                products.append({
                    "product": product,
                    "footage_keys": item.get("footage_keys", []),
                    "talking_points": item.get("talking_points", ""),
                })

        if not products:
            ls.status = "failed"
            ls.error_message = "No valid products in queue"
            await db.commit()
            return

        # Canvas dimensions
        from services.render_planner import get_canvas_dimensions
        canvas_w, canvas_h = get_canvas_dimensions(ls.output_format or "9:16")

        # Create HLS output directory
        hls_dir = f"/tmp/hls/{ls.stream_key}"
        os.makedirs(hls_dir, exist_ok=True)

        ls.status = "live"
        ls.hls_url = f"/hls/{ls.stream_key}/stream.m3u8"
        await db.commit()

        product_idx = ls.current_product_index or 0
        paragraph_count = ls.total_paragraphs_generated or 0

        try:
            while True:
                # Refresh session status from DB
                await db.refresh(ls)
                if ls.status in ("ended", "failed"):
                    break
                if ls.status == "paused":
                    await asyncio.sleep(2)
                    continue

                # Check duration limit
                if ls.started_at:
                    elapsed = (datetime.utcnow() - ls.started_at).total_seconds()
                    if elapsed > (ls.max_duration_minutes or 60) * 60:
                        logger.info("Session %s reached max duration", session_id)
                        break

                # Pick current product (cycle)
                current = products[product_idx % len(products)]
                product = current["product"]

                # Step 1: Generate script paragraph via Claude
                script = await _generate_paragraph(
                    product=product,
                    talking_points=current["talking_points"],
                    corpus_context=corpus_context,
                    voice_style=ls.voice_style_notes or "",
                    paragraph_index=paragraph_count,
                )
                logger.info(json.dumps({
                    "service": "live_session", "level": "info",
                    "message": f"Session {session_id} para {paragraph_count}: {script[:80]}",
                    "timestamp": datetime.utcnow().isoformat(),
                }))

                # Step 2: Generate TTS audio
                audio_path = os.path.join(hls_dir, f"para_{paragraph_count}.wav")
                await _generate_tts(
                    text=script,
                    avatar=avatar,
                    reference_audio_b64=reference_audio_b64,
                    output_path=audio_path,
                )

                # Step 3: Pick footage
                footage_key = _pick_footage(current["footage_keys"], paragraph_count)

                # Step 4: FFmpeg composite -> HLS segment
                await _feed_segment_to_ffmpeg(
                    audio_path, footage_key, canvas_w, canvas_h,
                    hls_dir, paragraph_count,
                )

                paragraph_count += 1
                product_idx += 1

                # Update session state
                ls.current_product_index = product_idx
                ls.total_paragraphs_generated = paragraph_count
                ls.current_paragraph = script
                await db.commit()

        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Live session %s crashed: %s", session_id, e)
            ls.error_message = str(e)[:500]
        finally:
            ls.status = "ended"
            ls.ended_at = datetime.utcnow()
            await db.commit()
            logger.info(json.dumps({
                "service": "live_session", "level": "info",
                "message": f"Session {session_id} ended. {paragraph_count} paragraphs.",
                "timestamp": datetime.utcnow().isoformat(),
            }))


async def _download_reference_audio(audio_r2_key: str) -> str:
    """Download reference audio from R2 and return base64-encoded string."""
    from services.r2_storage import get_r2_storage_service
    import httpx

    r2 = get_r2_storage_service()
    url = r2.get_public_url(audio_r2_key)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("utf-8")


async def _generate_paragraph(product, talking_points, corpus_context, voice_style, paragraph_index):
    """Generate one paragraph of live selling script via Claude."""
    import anthropic
    from config import settings

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    system = (
        "You are generating a live TikTok Shop selling script one paragraph at a time. "
        "Each paragraph is 2-4 sentences, spoken aloud by the creator's AI voice clone. "
        "Be natural, energetic, and conversational. Avoid AI-sounding phrases.\n\n"
    )
    if corpus_context:
        system += "CREATOR'S SPEAKING STYLE EXAMPLES:\n" + corpus_context[:3000] + "\n\n"
    if voice_style:
        system += "VOICE STYLE NOTES: " + voice_style + "\n\n"
    system += (
        "RULES:\n"
        "- Each paragraph is self-contained (viewer may have just joined)\n"
        "- Mention the product name and one key benefit\n"
        "- Include a soft call-to-action every 3rd paragraph\n"
        "- Vary your energy - don't be monotone\n"
        "- Use contractions and informal language\n"
        "- Output ONLY the paragraph text, nothing else"
    )

    price_str = "?"
    if product.price:
        price_str = str(product.price)
    elif product.current_price:
        price_str = str(product.current_price)

    desc = (product.description or "")[:300]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        temperature=0.8,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                "Product: " + product.name + "\n"
                "Price: $" + price_str + "\n"
                "Description: " + desc + "\n"
                "Talking points: " + talking_points + "\n"
                "This is paragraph #" + str(paragraph_index + 1) + " of the stream.\n"
                "Generate the next paragraph."
            ),
        }],
    )
    return response.content[0].text.strip()


async def _generate_tts(text: str, avatar, reference_audio_b64, output_path: str):
    """Generate TTS audio via Fish Speech on GPU server and write to output_path."""
    from services.gpu_server import get_gpu_server_client
    import httpx

    gpu = get_gpu_server_client()

    if gpu and reference_audio_b64:
        try:
            result = await gpu.fish_speech_tts(
                text=text,
                reference_audio_b64=reference_audio_b64,
                format="wav",
                input_duration_seconds=max(len(text) / 15.0, 5.0),
            )
            # Result has audio_base64
            if result.get("audio_base64"):
                audio_bytes = base64.b64decode(result["audio_base64"])
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)
                return
            elif result.get("audio_r2_key"):
                from services.r2_storage import get_r2_storage_service
                r2 = get_r2_storage_service()
                url = r2.get_public_url(result["audio_r2_key"])
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(url)
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                return
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("GPU Fish Speech TTS failed, using fallback silence: %s", e)

    # Fallback: generate silence if no GPU or TTS fails
    dur = max(len(text) / 15.0, 3.0)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"anullsrc=r=44100:cl=mono:d={dur:.1f}",
        "-c:a", "pcm_s16le", output_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _feed_segment_to_ffmpeg(audio_path, footage_key, w, h, hls_dir, segment_idx):
    """Generate one HLS .ts segment from audio + footage."""
    segment_path = os.path.join(hls_dir, f"segment_{segment_idx:06d}.ts")

    # Get audio duration
    probe = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", audio_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await probe.communicate()
    duration = float(stdout.decode().strip() or "5")

    cmd = None
    if footage_key:
        # Download footage from R2
        footage_path = os.path.join(hls_dir, f"footage_{segment_idx}.mp4")
        try:
            from services.r2_storage import get_r2_storage_service
            import httpx
            r2 = get_r2_storage_service()
            url = r2.get_public_url(footage_key)
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                with open(footage_path, "wb") as f:
                    f.write(resp.content)

            cmd = [
                "ffmpeg", "-y",
                "-i", footage_path,
                "-i", audio_path,
                "-t", str(duration),
                "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p", "-g", "50", "-keyint_min", "50",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-f", "mpegts", segment_path,
            ]
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("Failed to download footage %s: %s, using solid bg", footage_key, e)
            cmd = None

    if cmd is None:
        # Solid color background with audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={w}x{h}:d={duration}",
            "-i", audio_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-shortest",
            "-f", "mpegts", segment_path,
        ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("FFmpeg segment %d failed: %s", segment_idx, stderr.decode()[-300:])
        return

    # Update HLS playlist
    _update_hls_playlist(hls_dir, segment_idx, duration)
    logger.info(json.dumps({
        "service": "live_session", "level": "info",
        "message": f"HLS segment {segment_idx} written: {duration:.1f}s",
        "timestamp": datetime.utcnow().isoformat(),
    }))

    # Clean up footage download
    footage_path = os.path.join(hls_dir, f"footage_{segment_idx}.mp4")
    if os.path.exists(footage_path):
        os.remove(footage_path)


def _update_hls_playlist(hls_dir, segment_idx, duration):
    """Write/update the m3u8 playlist file with sliding window."""
    playlist_path = os.path.join(hls_dir, "stream.m3u8")

    window_size = 10
    start_idx = max(0, segment_idx - window_size + 1)

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:" + str(int(duration) + 1),
        "#EXT-X-MEDIA-SEQUENCE:" + str(start_idx),
    ]
    for i in range(start_idx, segment_idx + 1):
        seg_file = f"segment_{i:06d}.ts"
        if os.path.exists(os.path.join(hls_dir, seg_file)):
            lines.append("#EXTINF:" + f"{duration:.3f}" + ",")
            lines.append(seg_file)

    with open(playlist_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _pick_footage(footage_keys: list, paragraph_index: int):
    """Pick the next footage clip, cycling through the list."""
    if not footage_keys:
        return None
    return footage_keys[paragraph_index % len(footage_keys)]
