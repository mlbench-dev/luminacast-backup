import React from 'react';
import {Composition} from 'remotion';
import {
	CaptionComposition,
	CaptionCompositionProps,
	DEFAULT_CAPTION_COMPOSITION_PROPS,
	computeDurationInFrames,
} from './CaptionComposition';

export const CAPTION_COMPOSITION_ID = 'CaptionOverlay';

// Canvas size + fps are render-time inputs (the worker passes the slot's
// resolution), so width/height/durationInFrames are resolved per render via
// calculateMetadata from the caption + render props rather than hardcoded.
export type CaptionRenderProps = CaptionCompositionProps & {
	width: number;
	height: number;
	fps: number;
};

export const DEFAULT_CAPTION_RENDER_PROPS: CaptionRenderProps = {
	...DEFAULT_CAPTION_COMPOSITION_PROPS,
	width: 1080,
	height: 1920,
	fps: 30,
};

export const RemotionRoot: React.FC = () => {
	return (
		<Composition<CaptionRenderProps>
			id={CAPTION_COMPOSITION_ID}
			component={CaptionComposition}
			defaultProps={DEFAULT_CAPTION_RENDER_PROPS}
			// Placeholders; overwritten by calculateMetadata at render time.
			durationInFrames={1}
			fps={30}
			width={1080}
			height={1920}
			calculateMetadata={({props}) => {
				const fps = props.fps || 30;
				return {
					fps,
					width: props.width || 1080,
					height: props.height || 1920,
					durationInFrames: computeDurationInFrames(props.tokens, fps),
				};
			}}
		/>
	);
};
