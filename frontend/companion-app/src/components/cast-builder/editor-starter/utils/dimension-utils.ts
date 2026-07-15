import {DropPosition} from '../assets/add-asset';
import {fitElementSizeInContainer} from './fit-element-size-in-container';

interface CalculateMediaDimensionsForCanvasParams {
	mediaWidth: number;
	mediaHeight: number;
	containerWidth: number;
	containerHeight: number;
	dropPosition: DropPosition | null;
}

// Default insertion size cap. When a media file's intrinsic aspect ratio
// matches the composition (e.g. 9:16 video into a 9:16 canvas), the raw
// fit-into-container math returns FULLSCREEN. That's almost never what the
// user wanted — it covers the avatar entirely and acts like a base-track
// replacement, not an overlay. Cap insertion at 60% of the smaller axis so
// new media drops in clearly as a PIP. Users can still drag the resize
// handles to make it bigger if they really do want fullscreen.
const MAX_INITIAL_FILL_RATIO = 0.6;

export const calculateMediaDimensionsForCanvas = ({
	mediaWidth,
	mediaHeight,
	containerWidth,
	containerHeight,
	dropPosition,
}: CalculateMediaDimensionsForCanvasParams): {
	width: number;
	height: number;
	top: number;
	left: number;
} => {
	// First fit the media into a CAPPED container, not the full canvas. This
	// avoids the fullscreen-default trap for aspect-matching media.
	const capW = containerWidth * MAX_INITIAL_FILL_RATIO;
	const capH = containerHeight * MAX_INITIAL_FILL_RATIO;
	const dimensions = fitElementSizeInContainer({
		containerSize: {
			width: capW,
			height: capH,
		},
		elementSize: {
			width: mediaWidth,
			height: mediaHeight,
		},
	});

	const left = dropPosition
		? dropPosition.x - dimensions.width / 2
		: (containerWidth - dimensions.width) / 2;
	const top = dropPosition
		? dropPosition.y - dimensions.height / 2
		: (containerHeight - dimensions.height) / 2;

	return {
		width: Math.round(dimensions.width),
		height: Math.round(dimensions.height),
		top: Math.round(top),
		left: Math.round(left),
	};
};
