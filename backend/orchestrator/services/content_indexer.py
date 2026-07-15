"""Content indexing pipeline for channels — scrape, filter, transcribe, build voice profile."""

import asyncio
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from config import settings

logger = logging.getLogger(__name__)


class ContentIndexer:
    """5-stage pipeline: Scrape → Pre-filter → Transcribe → Post-filter → Voice profile."""

    MEME_SIGNALS = [
        "#meme", "#comedy", "#skit", "#duet", "#stitch",
        "#challenge", "#trend", "#dance", "#pov", "#storytime",
        "#greenscreen", "#fyp",
    ]
    FILLER_LIST = [
        "like", "literally", "honestly", "basically", "you know",
        "i mean", "right", "so yeah", "kind of", "sort of",
        "actually", "obviously", "just", "really", "totally",
        "um", "uh", "okay so", "well",
    ]

    async def index_channel(self, channel_id: str):
        """Run full indexing pipeline for a channel."""
        from database import async_session_factory
        from models.channel import Channel, ChannelTranscript

        async with async_session_factory() as db:
            channel = await db.get(Channel, channel_id)
            if not channel:
                logger.error(f"Channel {channel_id} not found")
                return

            try:
                channel.index_status = "indexing"
                await db.commit()

                # Stage 1: Scrape video list
                videos = await self._scrape_videos(channel)
                if not videos:
                    channel.index_status = "completed"
                    channel.voice_profile = {"tone": "unknown", "status": "no_videos_found", "indexed_count": 0}
                    await db.commit()
                    return

                channel.index_target_count = len(videos)
                await db.commit()

                transcripts = []
                for i, video in enumerate(videos):
                    # Stage 2: Pre-filter
                    is_relevant, reason = self._pre_filter(video)

                    transcript = ChannelTranscript(
                        id=f"ct_{uuid4().hex[:12]}",
                        channel_id=channel_id,
                        video_url=video.get("video_url", video.get("url", "")),
                        video_title=video.get("description", video.get("title", "")),
                        video_duration_seconds=video.get("duration", 0),
                        video_views=video.get("stats", {}).get("views", 0) if isinstance(video.get("stats"), dict) else 0,
                    )

                    if not is_relevant:
                        transcript.is_relevant = False
                        transcript.relevance_reason = reason
                        transcript.status = "filtered_out"
                    else:
                        # Stage 3: We simulate transcription for videos
                        # In production this would download audio + whisper + diarization
                        # For now, use the video description/caption as transcript proxy
                        text = video.get("description", "") or video.get("text", "")
                        transcript.transcript_text = text
                        transcript.word_count = len(text.split()) if text else 0
                        transcript.speech_ratio = 0.8  # assume good until we can check
                        transcript.has_main_speaker = True
                        transcript.status = "completed"

                        # Stage 4: Post-filter
                        is_relevant_post, reason_post = self._post_filter(transcript)
                        if not is_relevant_post:
                            transcript.is_relevant = False
                            transcript.relevance_reason = reason_post
                            transcript.status = "filtered_out"

                    db.add(transcript)
                    transcripts.append(transcript)

                    # Update progress
                    channel.indexed_video_count = i + 1
                    if (i + 1) % 5 == 0 or i == len(videos) - 1:
                        await db.commit()

                # Count relevant
                relevant_count = sum(1 for t in transcripts if t.is_relevant)
                channel.relevant_video_count = relevant_count

                # Stage 5: Build voice profile
                voice_profile = self._build_voice_profile(transcripts)
                channel.voice_profile = voice_profile
                channel.index_status = "completed"
                channel.last_indexed_at = datetime.now(timezone.utc)
                await db.commit()

                logger.info(json.dumps({
                    "service": "content_indexer",
                    "message": f"Channel {channel_id} indexing complete",
                    "total": len(transcripts),
                    "relevant": relevant_count,
                    "filtered_out": len(transcripts) - relevant_count,
                }))

            except Exception as e:
                logger.error(f"Channel indexing failed: {e}")
                channel.index_status = "failed"
                await db.commit()
                raise

    async def _scrape_videos(self, channel) -> list:
        """Stage 1: Scrape video list from TikTok via Apify."""
        from services.apify_tiktok import get_apify_tiktok_service

        handle = channel.handle.lstrip("@")
        tiktok_url = f"https://www.tiktok.com/@{handle}"

        service = get_apify_tiktok_service()
        videos = await service.fetch_tiktok_videos(tiktok_url, max_videos=50)
        return videos or []

    def _pre_filter(self, video: dict) -> tuple:
        """Stage 2: Pre-filter by metadata. Returns (is_relevant, reason)."""
        title = (video.get("description", "") or video.get("title", "") or "").lower()
        duration = video.get("duration", 0) or 0

        if duration < 10:
            return False, "too_short"

        caption_lower = title
        meme_count = sum(1 for tag in self.MEME_SIGNALS if tag in caption_lower)
        if meme_count >= 2:
            return False, "meme_detected"

        if "#duet" in caption_lower or "#stitch" in caption_lower:
            return False, "duet_or_stitch"

        return True, ""

    def _post_filter(self, transcript) -> tuple:
        """Stage 4: Post-filter by content. Returns (is_relevant, reason)."""
        if transcript.speech_ratio < 0.3:
            return False, "speech_ratio_low"

        if not transcript.has_main_speaker:
            return False, "no_main_speaker"

        if transcript.word_count < 20:
            return False, "too_few_words"

        return True, ""

    def _build_voice_profile(self, transcripts: list) -> dict:
        """Stage 5: Aggregate relevant transcripts into voice profile."""
        relevant = [t for t in transcripts if t.is_relevant and t.transcript_text]

        if len(relevant) < 3:
            return {
                "tone": "unknown",
                "status": "insufficient_data",
                "indexed_count": len(relevant),
                "indexed_relevant_videos": len(relevant),
                "indexed_total_videos": len(transcripts),
                "filtered_out_count": len(transcripts) - len(relevant),
            }

        analyses = []
        for t in relevant:
            analysis = self._analyze_transcript(t.transcript_text)
            t.analysis = analysis
            analyses.append(analysis)

        all_texts = [t.transcript_text for t in relevant]

        # Common phrases
        all_phrases = []
        for a in analyses:
            all_phrases.extend(a.get("common_phrases", []))
        phrase_counts = Counter(all_phrases)
        common_phrases = [p for p, c in phrase_counts.most_common(50) if c >= 2]

        # Sentence starters
        all_starters = []
        for a in analyses:
            all_starters.extend(a.get("sentence_starters", []))
        common_starters = [s for s, c in Counter(all_starters).most_common(15) if c >= 2]

        # Sign-offs
        sign_offs = self._extract_sign_offs(all_texts)

        # Filler words
        detected_fillers = [p for p in common_phrases if p.lower() in self.FILLER_LIST]

        # Sample phrases
        sample_phrases = self._extract_sample_phrases(all_texts, common_phrases)

        # Aggregate stats
        avg_sl = sum(a.get("avg_sentence_length", 10) for a in analyses) / len(analyses)
        avg_qf = sum(a.get("question_frequency", 0) for a in analyses) / len(analyses)
        avg_ef = sum(a.get("exclamation_frequency", 0) for a in analyses) / len(analyses)

        energies = [a.get("energy", "medium") for a in analyses]
        energy_counts = Counter(energies)
        avg_energy = energy_counts.most_common(1)[0][0] if energy_counts else "medium"

        # Vocabulary level
        total_words = sum(a.get("word_count", 0) for a in analyses)
        unique_words = len(set(" ".join(all_texts).lower().split()))
        vocab_ratio = unique_words / max(total_words, 1)
        vocab_level = "advanced" if vocab_ratio > 0.5 else "intermediate" if vocab_ratio > 0.3 else "casual"

        # Topics — extract from text
        topics = self._extract_topics(all_texts)

        # CTA style
        cta_style = self._detect_cta_style(all_texts)

        # Tone description
        tone = self._describe_tone(analyses, avg_energy)

        return {
            "tone": tone,
            "avg_sentence_length": round(avg_sl, 1),
            "common_phrases": common_phrases[:25],
            "filler_words": detected_fillers[:10],
            "question_frequency": round(avg_qf, 2),
            "exclamation_frequency": round(avg_ef, 2),
            "vocabulary_level": vocab_level,
            "topics": topics[:15],
            "cta_style": cta_style,
            "avg_energy": avg_energy,
            "sentence_starters": common_starters[:10],
            "sign_offs": sign_offs[:8],
            "language": "en",
            "sample_phrases": sample_phrases[:10],
            "indexed_relevant_videos": len(relevant),
            "indexed_total_videos": len(transcripts),
            "filtered_out_count": len(transcripts) - len(relevant),
        }

    def _analyze_transcript(self, text: str) -> dict:
        """Analyze a single transcript."""
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        words = text.lower().split()
        word_counts = [len(s.split()) for s in sentences]
        questions = [s for s in sentences if '?' in s]
        exclamations = sum(1 for c in text if c == '!')

        # N-grams
        bigrams = [' '.join(words[i:i+2]) for i in range(len(words)-1)]
        trigrams = [' '.join(words[i:i+3]) for i in range(len(words)-2)]

        common_bigrams = [p for p, c in Counter(bigrams).most_common(30) if c >= 2]
        common_trigrams = [p for p, c in Counter(trigrams).most_common(15) if c >= 2]

        # Sentence starters
        starters = [s.split()[0] if s.split() else '' for s in sentences]
        common_starters = [w for w, c in Counter(starters).most_common(10) if c >= 2]

        # Energy
        caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
        excl_ratio = exclamations / max(len(sentences), 1)
        energy = ("high" if caps_ratio > 0.08 or excl_ratio > 0.3 else
                  "medium" if excl_ratio > 0.15 else "low")

        return {
            "sentence_count": len(sentences),
            "avg_sentence_length": round(sum(word_counts) / max(len(word_counts), 1), 1),
            "question_count": len(questions),
            "exclamation_count": exclamations,
            "question_frequency": round(len(questions) / max(len(sentences), 1), 2),
            "exclamation_frequency": round(excl_ratio, 2),
            "common_phrases": common_bigrams[:15] + common_trigrams[:10],
            "sentence_starters": common_starters,
            "energy": energy,
            "word_count": len(words),
        }

    def _extract_sign_offs(self, texts: list) -> list:
        """Extract common closing phrases."""
        sign_off_patterns = [
            "follow", "subscribe", "like", "share", "tap that",
            "link in bio", "check it out", "see you", "bye",
            "don't forget", "thanks for watching", "love you",
            "grab it", "get yours", "shop now",
        ]
        found = []
        for text in texts:
            lower = text.lower()
            last_50 = lower[-200:] if len(lower) > 200 else lower
            for pattern in sign_off_patterns:
                if pattern in last_50 and pattern not in found:
                    found.append(pattern)
        return found

    def _extract_sample_phrases(self, texts: list, common_phrases: list) -> list:
        """Extract characteristic sample phrases."""
        samples = []
        for text in texts:
            sentences = re.split(r'[.!?]+', text)
            for s in sentences:
                s = s.strip()
                if len(s) > 15 and len(s) < 100:
                    lower = s.lower()
                    if any(p in lower for p in common_phrases[:10]):
                        if s not in samples:
                            samples.append(s)
                            if len(samples) >= 10:
                                return samples
        # If we don't have enough from phrase matching, take interesting sentences
        for text in texts:
            sentences = re.split(r'[.!?]+', text)
            for s in sentences:
                s = s.strip()
                if 20 < len(s) < 100 and s not in samples:
                    samples.append(s)
                    if len(samples) >= 10:
                        return samples
        return samples

    def _extract_topics(self, texts: list) -> list:
        """Extract topic keywords from texts."""
        topic_keywords = {
            "beauty": ["beauty", "makeup", "skincare", "lipstick", "foundation", "blush", "mascara"],
            "fashion": ["fashion", "outfit", "dress", "style", "clothes", "wearing"],
            "tech": ["tech", "phone", "laptop", "gadget", "device", "app"],
            "food": ["food", "recipe", "cooking", "delicious", "taste", "eat"],
            "fitness": ["workout", "fitness", "gym", "exercise", "protein", "health"],
            "home": ["home", "kitchen", "organize", "cleaning", "decor", "furniture"],
            "deals": ["deal", "sale", "discount", "coupon", "save", "price drop"],
            "skincare": ["serum", "moisturizer", "cleanser", "SPF", "retinol", "vitamin c"],
            "haircare": ["hair", "shampoo", "conditioner", "curls", "straighten"],
            "wellness": ["wellness", "meditation", "sleep", "stress", "mental health"],
            "gadgets": ["gadget", "tool", "hack", "amazon find", "must have"],
            "reviews": ["review", "honest", "trying", "first impression", "unboxing"],
        }

        combined = " ".join(texts).lower()
        found = []
        for topic, keywords in topic_keywords.items():
            if any(kw in combined for kw in keywords):
                found.append(topic)
        return found

    def _detect_cta_style(self, texts: list) -> str:
        """Detect the creator's call-to-action style."""
        combined = " ".join(texts).lower()
        if "link in bio" in combined:
            return "link_in_bio"
        if "shop now" in combined or "grab it" in combined:
            return "direct_shop"
        if "follow" in combined and "more" in combined:
            return "follow_for_more"
        return "general"

    def _describe_tone(self, analyses: list, energy: str) -> str:
        """Generate a tone description."""
        avg_qf = sum(a.get("question_frequency", 0) for a in analyses) / max(len(analyses), 1)
        descriptors = []

        if energy == "high":
            descriptors.append("enthusiastic")
        elif energy == "medium":
            descriptors.append("upbeat")
        else:
            descriptors.append("calm")

        if avg_qf > 0.2:
            descriptors.append("conversational")
        else:
            descriptors.append("direct")

        descriptors.append(f"{energy}-energy")
        return ", ".join(descriptors)
