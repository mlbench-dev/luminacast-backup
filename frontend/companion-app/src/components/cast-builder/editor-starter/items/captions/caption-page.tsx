import {TikTokPage} from '@remotion/captions';
import {fitTextOnNLines} from '@remotion/layout-utils';
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {turnFontStyleIntoCss} from '../../inspector/controls/font-style-controls/font-style-controls';
import {FontStyle, TextAlign, TextDirection} from '../text/text-item-type';
import {
	CAPTION_PRESETS,
	CaptionPreset,
	getCaptionPreset,
} from '@/lib/captionPresets';

// PR #8 invariant: when an item lacks an explicit color, we fall back to
// white instead of inheriting from the parent container. Don't change.
const SAFE_COLOR = (c: string | undefined | null) => c ?? 'white';

// Per-preset word styling. Lifts the spec's getWordStyle switch into a
// pure helper so the same logic drives both the editor preview and the
// preset-chip live previews.
export function getWordStyle(
	preset: CaptionPreset,
	isActive: boolean,
	frame: number,
	fps: number,
	wordIndex: number,
): React.CSSProperties {
	const base: React.CSSProperties = {
		color: isActive ? preset.highlightColor : SAFE_COLOR(preset.color),
		fontFamily: preset.fontFamily,
		fontWeight: preset.fontWeight,
		fontSize: preset.fontSize,
		WebkitTextStroke: preset.strokeWidth
			? `${preset.strokeWidth}px ${preset.strokeColor}`
			: undefined,
		textShadow: preset.shadow,
		textTransform: preset.textTransform,
		transition: 'color 0.1s, transform 0.15s, opacity 0.15s',
		display: 'inline-block',
		padding: '0 3px',
		paintOrder: 'stroke',
	};

	switch (preset.animation) {
		case 'word_highlight':
			// Plain color swap (Hormozi). Default is already correct.
			return base;
		case 'slide_in':
			return {
				...base,
				transform: isActive ? 'translateY(0)' : 'translateY(0)',
				opacity: 1,
			};
		case 'word_bounce':
			return {
				...base,
				transform: isActive
					? 'scale(1.2) translateY(-4px)'
					: 'scale(1)',
			};
		case 'fade_in':
			return {
				...base,
				opacity: isActive ? 1 : 0.85,
			};
		case 'glow_pulse': {
			const glowIntensity = isActive ? 1 : 0.3;
			return {
				...base,
				textShadow: `0 0 ${20 * glowIntensity}px ${SAFE_COLOR(preset.color)}, 0 0 ${
					40 * glowIntensity
				}px ${SAFE_COLOR(preset.color)}`,
			};
		}
		case 'typewriter':
			// True typewriter is per-char; we approximate with a fade in
			// on the active token so untouched tokens are dim.
			return {
				...base,
				opacity: isActive ? 1 : 0.5,
			};
		case 'fade_slide_up':
			return {
				...base,
				transform: isActive ? 'translateY(0)' : 'translateY(2px)',
				opacity: isActive ? 1 : 0.85,
			};
		case 'slam_in':
			return {
				...base,
				transform: isActive ? 'scale(1.05)' : 'scale(1)',
				opacity: isActive ? 1 : 0.9,
			};
		case 'scribble_in':
			return {
				...base,
				transform: isActive
					? `rotate(${Math.sin(frame / fps * 12 + wordIndex) * 1.5}deg)`
					: 'rotate(0deg)',
			};
		case 'color_wave':
			return {
				...base,
				color: isActive ? preset.highlightColor : SAFE_COLOR(preset.color),
				transition: 'color 0.3s ease',
			};
		case 'word_box':
			return {
				...base,
				backgroundColor: isActive ? 'rgba(0,0,0,0.7)' : 'transparent',
				borderRadius: isActive ? '6px' : '0',
				padding: isActive ? '2px 10px' : '0 3px',
			};
		case 'fill_on_active':
			return {
				...base,
				color: isActive ? preset.highlightColor : 'transparent',
				WebkitTextStroke: `${preset.strokeWidth}px ${preset.strokeColor}`,
			};
		case 'none':
			return base;
		case 'dim_inactive':
			return {
				...base,
				opacity: isActive ? 1 : 0.35,
			};
		case 'wobble':
			return {
				...base,
				transform: isActive
					? `rotate(${Math.sin(frame / fps * 8) * 3}deg) scale(1.1)`
					: 'rotate(0deg) scale(1)',
			};
		default: {
			// Exhaustiveness guard — every animation in CAPTION_PRESETS
			// must have a case above. We keep `base` as a safety net so a
			// future preset addition still renders text instead of nothing.
			return base;
		}
	}
}

export const CaptionPage: React.FC<{
	page: TikTokPage;
	captionWidth: number;
	fontFamily: string;
	fontStyle: FontStyle;
	lineHeight: number;
	letterSpacing: number;
	color: string;
	highlightColor: string;
	direction: TextDirection;
	align: TextAlign;
	fontSize: number;
	maxLines: number;
	strokeColor?: string;
	strokeWidth?: number;
	presetId?: string;
	// regr-2c: when set, the rendered text height is also constrained to the
	// canvas area above this bottom safe margin (percent). If the maxLines fit
	// still overflows that area, the font is auto-shrunk by up to 15% before we
	// risk clipping. Optional so the editor preview is unaffected when omitted.
	safeBottomPct?: number;
}> = ({
	page,
	captionWidth,
	fontFamily,
	fontStyle,
	lineHeight,
	letterSpacing,
	color,
	highlightColor,
	direction,
	align,
	fontSize: desiredFontSize,
	maxLines,
	strokeColor,
	strokeWidth,
	presetId,
	safeBottomPct,
}) => {
	const frame = useCurrentFrame();
	const {fps, height: canvasHeight} = useVideoConfig();
	const timeInMs = (frame / fps) * 1000;

	// Build a CaptionPreset that mirrors the item's flat fields so the
	// per-token styling honors the user's per-block overrides while
	// still falling back to the named preset's animation.
	const namedPreset =
		presetId && CAPTION_PRESETS[presetId]
			? CAPTION_PRESETS[presetId]
			: getCaptionPreset(null);

	const presetForRender: CaptionPreset = {
		...namedPreset,
		fontFamily: fontFamily || namedPreset.fontFamily,
		fontWeight: Number(fontStyle?.weight ?? namedPreset.fontWeight),
		fontSize: desiredFontSize,
		color: SAFE_COLOR(color),
		highlightColor: highlightColor || namedPreset.highlightColor,
		strokeColor: strokeColor ?? namedPreset.strokeColor,
		strokeWidth: strokeWidth ?? namedPreset.strokeWidth,
	};

	const fittedText = fitTextOnNLines({
		fontFamily,
		text: page.text,
		maxBoxWidth: captionWidth,
		maxLines: maxLines,
		maxFontSize: desiredFontSize,
	});

	let fontSize = Math.min(desiredFontSize, fittedText.fontSize);

	// regr-2c auto-shrink fail-safe: even after width-fitting onto maxLines, a
	// block of `maxLines` at this size and lineHeight may be taller than the
	// safe area (the canvas height minus the bottom margin). If so, shrink the
	// font by up to 15% to bring it back inside before we risk clipping the
	// last line. Beyond 15% we stop shrinking (clarity over a perfect fit) and
	// rely on the upstream char-budget chunking to have split the text.
	if (typeof safeBottomPct === 'number' && safeBottomPct > 0 && canvasHeight > 0) {
		const safeHeight = canvasHeight * (1 - safeBottomPct / 100);
		const blockHeight = fontSize * lineHeight * maxLines;
		if (blockHeight > safeHeight && safeHeight > 0) {
			const scale = Math.max(0.85, safeHeight / blockHeight);
			fontSize = fontSize * scale;
		}
	}

	const containerStyle: React.CSSProperties = React.useMemo(
		() => ({
			fontSize: fontSize,
			color: SAFE_COLOR(color),
			fontFamily,
			height: '100%',
			width: '100%',
			...turnFontStyleIntoCss(fontStyle),
			lineHeight: String(lineHeight),
			letterSpacing: `${letterSpacing}px`,
			textAlign: align,
			display: 'flex',
			alignItems: 'center',
			justifyContent: 'center',
		}),
		[fontSize, fontFamily, fontStyle, lineHeight, letterSpacing, align, color],
	);

	return (
		<AbsoluteFill>
			<div dir={direction} style={containerStyle}>
				<span>
					{page.tokens.map((t, i) => {
						const startRelativeToSequence = t.fromMs - page.startMs;
						const endRelativeToSequence = t.toMs - page.startMs;

						const active =
							startRelativeToSequence <= timeInMs &&
							endRelativeToSequence > timeInMs;

						const wordStyle = getWordStyle(
							{...presetForRender, fontSize},
							active,
							frame,
							fps,
							i,
						);

						return (
							<span
								key={t.fromMs + t.text}
								style={{
									...wordStyle,
									whiteSpace: 'pre-wrap',
								}}
							>
								{t.text}
							</span>
						);
					})}
				</span>
			</div>
		</AbsoluteFill>
	);
};
