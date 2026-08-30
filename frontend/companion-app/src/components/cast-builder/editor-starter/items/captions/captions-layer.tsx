import {createTikTokStyleCaptions, type TikTokPage} from '@remotion/captions';
import React, {useContext, useMemo} from 'react';
import {Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {CaptionAsset} from '../../assets/assets';
import {TextItemHoverPreviewContext} from '../../context-provider';
import {FontInfoContext} from '../../utils/text/font-info';
import {useLoadFontFromTextItem} from '../../utils/text/load-font-from-text-item';
import {useAssetFromAssetId} from '../../utils/use-context';
import {EditorStarterItem} from '../item-type';
import {
	calculateFadeInOpacity,
	calculateFadeOutOpacity,
} from '../video/calculate-fade';
import {CaptionPage} from './caption-page';
import {overrideCaptionsItemWithHoverPreview} from './override-captions-item-with-hover-preview';

const SWITCH_CAPTIONS_EVERY_MS = 1200;

// Rough single-line character estimate — mirrors the backend composer's
// _chars_per_line (_AVG_GLYPH_EM = 0.52) so the preview and the render split
// pages the same way.
const AVG_GLYPH_EM = 0.52;

/**
 * Re-split a time-windowed caption page whose text is longer than one visible
 * chunk (`maxChars`) so each chunk fits `maxLines` at the current font size /
 * width — the SAME re-chunk the SSR renderer (capPageToCharBudget) and backend
 * composer (cap_tokens_to_line_budget) do. Without this the editor preview
 * showed a whole multi-second page wrapped onto 2+ lines while the final video
 * paged one line at a time. Each chunk keeps its source token timestamps, so it
 * starts exactly when its first word is spoken.
 */
function capPageToCharBudget(page: TikTokPage, maxChars: number): TikTokPage[] {
	if (!maxChars || maxChars <= 0 || page.text.length <= maxChars) {
		return [page];
	}
	const out: TikTokPage[] = [];
	let current: TikTokPage['tokens'] = [];
	let currentLen = 0;
	const flush = () => {
		if (current.length === 0) return;
		out.push({
			text: current.map((t) => t.text).join('').trim(),
			startMs: current[0].fromMs,
			durationMs: current[current.length - 1].toMs - current[0].fromMs,
			tokens: current,
		});
	};
	for (const tok of page.tokens) {
		if (current.length > 0 && currentLen + tok.text.length > maxChars) {
			flush();
			current = [];
			currentLen = 0;
		}
		current.push(tok);
		currentLen += tok.text.length;
	}
	flush();
	return out.length > 0 ? out : [page];
}

export const CaptionsLayer = ({
	item: itemWithoutHoverPreview,
}: {
	item: EditorStarterItem;
}) => {
	if (itemWithoutHoverPreview.type !== 'captions') {
		throw new Error('Item is not captions');
	}

	const textItemHoverPreview = useContext(TextItemHoverPreviewContext);
	const item = useMemo(() => {
		return overrideCaptionsItemWithHoverPreview(
			itemWithoutHoverPreview,
			textItemHoverPreview,
		);
	}, [itemWithoutHoverPreview, textItemHoverPreview]);

	const captionAsset = useAssetFromAssetId(item.assetId) as CaptionAsset;
	const context = useContext(FontInfoContext);

	const loaded = useLoadFontFromTextItem({
		fontFamily: item.fontFamily,
		fontVariant: item.fontStyle.variant,
		fontWeight: item.fontStyle.weight,
		fontInfosDuringRendering: context[item.fontFamily] ?? null,
	});

	const frame = useCurrentFrame();
	const {fps, durationInFrames: totalDurationInFrames} = useVideoConfig();

	const opacity = useMemo(() => {
		const inOpacity = calculateFadeInOpacity({
			currentFrame: frame,
			fadeInDurationInSeconds: item.fadeInDurationInSeconds,
			framesPerSecond: fps,
		});
		const outOpacity = calculateFadeOutOpacity({
			currentFrame: frame,
			fadeOutDurationInSeconds: item.fadeOutDurationInSeconds,
			framesPerSecond: fps,
			totalDurationInFrames: totalDurationInFrames,
		});
		return inOpacity * outOpacity * item.opacity;
	}, [
		item.fadeInDurationInSeconds,
		fps,
		frame,
		item.opacity,
		totalDurationInFrames,
		item.fadeOutDurationInSeconds,
	]);

	const style: React.CSSProperties = useMemo(() => {
		return {
			position: 'absolute',
			left: item.left,
			top: item.top,
			width: item.width,
			height: item.height,
			opacity: opacity,
			transform: `rotate(${item.rotation}deg)`,
			WebkitTextStroke: item.strokeWidth
				? `${item.strokeWidth}px ${item.strokeColor}`
				: '0',
			paintOrder: 'stroke',
		};
	}, [item, opacity]);

	const {pages: rawPages} = createTikTokStyleCaptions({
		captions: captionAsset.captions,
		combineTokensWithinMilliseconds: item.pageDurationInMilliseconds,
	});
	// Split each time-windowed page down to what fits `maxLines` at this font
	// size / width, so the preview pages one line at a time exactly like the
	// final render (SSR capPageToCharBudget / composer cap_tokens_to_line_budget).
	const maxCharsPerChunk =
		Math.max(1, Math.floor(item.width / Math.max(1, item.fontSize * AVG_GLYPH_EM))) *
		Math.max(1, item.maxLines || 1);
	const pages = rawPages.flatMap((p) => capPageToCharBudget(p, maxCharsPerChunk));

	if (!loaded) {
		return null;
	}

	return (
		<div style={style} className="select-none">
			{pages.map((page, index) => {
				const nextPage = pages[index + 1] ?? null;
				const captionOffsetInSeconds = item.captionStartInSeconds;
				const subtitleStartFrame =
					(page.startMs / 1000) * fps - captionOffsetInSeconds * fps;
				const subtitleEndFrame = Math.min(
					nextPage
						? (nextPage.startMs / 1000) * fps - captionOffsetInSeconds * fps
						: Infinity,
					subtitleStartFrame + SWITCH_CAPTIONS_EVERY_MS,
				);
				const durationInFrames = subtitleEndFrame - subtitleStartFrame;
				if (durationInFrames <= 0) {
					return null;
				}

				return (
					<Sequence
						key={index}
						from={subtitleStartFrame}
						durationInFrames={durationInFrames}
					>
						<CaptionPage
							captionWidth={item.width}
							fontFamily={item.fontFamily}
							fontStyle={item.fontStyle}
							key={index}
							page={page}
							lineHeight={item.lineHeight}
							letterSpacing={item.letterSpacing}
							color={item.color}
							direction={item.direction}
							align={item.align}
							fontSize={item.fontSize}
							highlightColor={item.highlightColor}
							maxLines={item.maxLines}
							strokeColor={item.strokeColor}
							strokeWidth={item.strokeWidth}
							presetId={item.metadata?.caption_preset as string | undefined}
						/>
					</Sequence>
				);
			})}
		</div>
	);
};
