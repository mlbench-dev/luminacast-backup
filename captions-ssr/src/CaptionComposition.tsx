import {Caption, createTikTokStyleCaptions, TikTokPage} from '@remotion/captions';
import React from 'react';
import {AbsoluteFill, Sequence, useVideoConfig} from 'remotion';
import {CaptionPage} from './frontend-mirror/items/captions/caption-page';
import {
	CAPTION_PRESETS,
	getCaptionPreset,
} from '@/lib/captionPresets';

// One word timing as sent by the worker. Mirrors the FFmpeg burn path's
// props._captions_tokens shape (services/cast_ffmpeg_composer.py): each
// token is {text, startMs, endMs} from the WhisperX word timings. The
// legacy {startInSeconds, endInSeconds} shape the composer still accepts
// is normalised to startMs/endMs in normalizeTokens() before it reaches
// the composition, so this component only deals in milliseconds.
export type CaptionToken = {
	text: string;
	startMs: number;
	endMs: number;
};

// Props the worker POSTs (after normalisation). Defaults mirror the
// editor's CaptionsItem so an overlay rendered headless matches preview.
// width/height/fps are render-canvas inputs consumed by Root's
// calculateMetadata; they are accepted here too (and ignored) so the same
// object can be passed straight to <Composition>.
export type CaptionCompositionProps = {
	tokens: CaptionToken[];
	presetId: string;
	// Page grouping window — same knob as the editor's
	// pageDurationInMilliseconds (combineTokensWithinMilliseconds).
	pageDurationInMilliseconds: number;
	// When the active text run can stay on screen at most this long before
	// the page is forced to switch. Mirrors the preview's
	// SWITCH_CAPTIONS_EVERY_MS so page lifetimes line up.
	switchCaptionsEveryMs: number;
	fontFamily: string;
	fontStyleVariant: string;
	fontStyleWeight: string;
	lineHeight: number;
	letterSpacing: number;
	maxLines: number;
	captionWidth: number;
	// regr-2c: bottom safe-area margin as a percentage of canvas height. The
	// caption block is vertically centred within the area ABOVE this margin so
	// its last lines never crop off the bottom edge. Defaults to 18%.
	safeBottomPct?: number;
	// regr-2c: hard character budget per visible chunk. A time-windowed page
	// whose text exceeds this is re-split so it fits maxLines at the font size /
	// width. 0/undefined disables the cap (renderer falls back to time windows).
	maxCharsPerChunk?: number;
	width?: number;
	height?: number;
	fps?: number;
};

export const DEFAULT_CAPTION_COMPOSITION_PROPS: CaptionCompositionProps = {
	tokens: [],
	presetId: 'hormozi_bold',
	pageDurationInMilliseconds: 1200,
	switchCaptionsEveryMs: 1200,
	fontFamily: '',
	fontStyleVariant: 'normal',
	fontStyleWeight: '800',
	lineHeight: 1.2,
	letterSpacing: 0,
	maxLines: 2,
	captionWidth: 900,
	safeBottomPct: 18,
	maxCharsPerChunk: 0,
};

// regr-2c default: proportion of the canvas height reserved at the bottom so a
// centred caption block can't run off the bottom edge. Mirrors the
// orchestrator's CAPTIONS_SAFE_AREA_BOTTOM_PCT default.
const DEFAULT_SAFE_BOTTOM_PCT = 18;

// Build @remotion/captions Caption[] from worker tokens. timestampMs is the
// token midpoint (the library uses it only for tie-breaking page splits).
function tokensToCaptions(tokens: CaptionToken[]): Caption[] {
	return tokens
		.filter((t) => (t.text ?? '').trim().length > 0)
		.map((t) => {
			const startMs = Math.max(0, Math.round(t.startMs));
			let endMs = Math.round(t.endMs);
			if (endMs <= startMs) {
				endMs = startMs + 1;
			}
			return {
				text: t.text,
				startMs,
				endMs,
				timestampMs: Math.round((startMs + endMs) / 2),
				confidence: null,
			} satisfies Caption;
		});
}

// Total render length in frames is derived from the last token's end so the
// overlay is exactly as long as the spoken caption track (plus a tail).
export function computeDurationInFrames(
	tokens: CaptionToken[],
	fps: number,
	tailMs = 200,
): number {
	const lastEnd = tokens.reduce(
		(max, t) => Math.max(max, Math.round(t.endMs)),
		0,
	);
	return Math.max(1, Math.ceil(((lastEnd + tailMs) / 1000) * fps));
}

// Pages are built with the SAME createTikTokStyleCaptions call the editor
// uses (captions-layer.tsx), so the SSR overlay groups words into pages
// identically to the preview.
function buildPages(
	tokens: CaptionToken[],
	combineTokensWithinMilliseconds: number,
): TikTokPage[] {
	const {pages} = createTikTokStyleCaptions({
		captions: tokensToCaptions(tokens),
		combineTokensWithinMilliseconds,
	});
	return pages;
}

// regr-2c: re-split a time-windowed page whose text exceeds maxCharsPerChunk so
// each visible chunk fits maxLines at the configured font size / width. Token
// timestamps are preserved: a child page starts at its first token's fromMs and
// ends at its last token's toMs, so adjacent chunks never overlap. A single
// token longer than the budget becomes its own chunk (clip one word rather than
// lose it). When the page already fits (or the cap is disabled) it's returned
// unchanged.
export function capPageToCharBudget(
	page: TikTokPage,
	maxCharsPerChunk: number,
): TikTokPage[] {
	if (!maxCharsPerChunk || maxCharsPerChunk <= 0) {
		return [page];
	}
	if (page.text.length <= maxCharsPerChunk) {
		return [page];
	}

	const out: TikTokPage[] = [];
	let current: TikTokPage['tokens'] = [];
	let currentLen = 0;

	const flush = () => {
		if (current.length === 0) {
			return;
		}
		out.push({
			text: current.map((t) => t.text).join('').trim(),
			startMs: current[0].fromMs,
			durationMs: current[current.length - 1].toMs - current[0].fromMs,
			tokens: current,
		});
	};

	for (const tok of page.tokens) {
		const addedLen = tok.text.length;
		if (current.length > 0 && currentLen + addedLen > maxCharsPerChunk) {
			flush();
			current = [];
			currentLen = 0;
		}
		current.push(tok);
		currentLen += addedLen;
	}
	flush();

	return out.length > 0 ? out : [page];
}

export const CaptionComposition: React.FC<CaptionCompositionProps> = ({
	tokens,
	presetId,
	pageDurationInMilliseconds,
	switchCaptionsEveryMs,
	fontFamily,
	fontStyleVariant,
	fontStyleWeight,
	lineHeight,
	letterSpacing,
	maxLines,
	captionWidth,
	safeBottomPct,
	maxCharsPerChunk,
}) => {
	const {fps} = useVideoConfig();

	// Resolve the named preset; getCaptionPreset falls back to the default
	// when the id is unknown, matching the editor.
	const preset =
		(presetId && CAPTION_PRESETS[presetId]) || getCaptionPreset(presetId);

	const resolvedFontFamily = fontFamily || preset.fontFamily;
	const resolvedWeight = fontStyleWeight || String(preset.fontWeight);

	const resolvedSafeBottomPct =
		typeof safeBottomPct === 'number' && safeBottomPct >= 0
			? Math.min(45, safeBottomPct)
			: DEFAULT_SAFE_BOTTOM_PCT;

	// regr-2c: build time-windowed pages, then re-split any page that would
	// overflow maxLines so its tail words can't crop. The char budget is sent
	// by the orchestrator (maxCharsPerChunk); when absent the page is unchanged.
	const pages = buildPages(tokens, pageDurationInMilliseconds).flatMap((p) =>
		capPageToCharBudget(p, maxCharsPerChunk ?? 0),
	);

	// Transparent background: only the caption text is drawn, so the
	// renderer emits a transparent PNG sequence / alpha video the composer
	// can overlay 1:1 onto the canvas in Step 12.
	return (
		<AbsoluteFill style={{backgroundColor: 'transparent'}}>
			{pages.map((page, index) => {
				const nextPage = pages[index + 1] ?? null;
				const startFrame = (page.startMs / 1000) * fps;
				const endFrame = Math.min(
					nextPage ? (nextPage.startMs / 1000) * fps : Infinity,
					startFrame + (switchCaptionsEveryMs / 1000) * fps,
				);
				const durationInFrames = endFrame - startFrame;
				if (durationInFrames <= 0) {
					return null;
				}
				return (
					<Sequence
						key={index}
						from={Math.round(startFrame)}
						durationInFrames={Math.max(1, Math.round(durationInFrames))}
					>
						{/* regr-2c: reserve the bottom safe area so the centred
						    caption block stays above the bottom edge and can't
						    crop its last line. CaptionPage centres within this
						    inset region instead of the full canvas. */}
						<AbsoluteFill style={{bottom: `${resolvedSafeBottomPct}%`}}>
							<CaptionPage
								page={page}
								captionWidth={captionWidth}
								fontFamily={resolvedFontFamily}
								fontStyle={{variant: fontStyleVariant, weight: resolvedWeight}}
								lineHeight={lineHeight}
								letterSpacing={letterSpacing}
								color={preset.color}
								highlightColor={preset.highlightColor}
								direction="ltr"
								align="center"
								fontSize={preset.fontSize}
								maxLines={maxLines}
								strokeColor={preset.strokeColor}
								strokeWidth={preset.strokeWidth}
								presetId={preset.id}
								safeBottomPct={resolvedSafeBottomPct}
							/>
						</AbsoluteFill>
					</Sequence>
				);
			})}
		</AbsoluteFill>
	);
};
