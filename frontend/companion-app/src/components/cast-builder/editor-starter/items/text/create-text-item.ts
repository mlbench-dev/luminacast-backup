import {generateRandomId} from '../../utils/generate-random-id';
import {loadFontFromTextItem} from '../../utils/text/load-font-from-text-item';
import {getTextDimensions} from '../../utils/text/measure-text';
import {stringSeemsRightToLeft} from '../../utils/text/right-to-left';
import {EditorStarterItem} from '../item-type';
import {FontStyle} from './text-item-type';

const TEXT_DURATION_IN_FRAMES = 100;
export const DEFAULT_FONT_SIZE = 80;

export const createTextItem = async ({
	xOnCanvas,
	yOnCanvas,
	from,
	text,
	align,
}: {
	xOnCanvas: number;
	yOnCanvas: number;
	from: number;
	text: string;
	align: 'left' | 'center';
}): Promise<EditorStarterItem> => {
	const id = generateRandomId();
	const defaultFontFamily = 'Roboto';
	await loadFontFromTextItem({
		fontFamily: defaultFontFamily,
		fontVariant: 'normal',
		fontWeight: '400',
		fontInfosDuringRendering: null,
	});

	const defaultLineHeight = 1.2;
	const defaultLetterSpacing = 0;

	const fontStyle: FontStyle = {
		variant: 'normal',
		weight: '400',
	};

	const textDimensions = getTextDimensions({
		text,
		fontFamily: defaultFontFamily,
		fontSize: DEFAULT_FONT_SIZE,
		lineHeight: defaultLineHeight,
		letterSpacing: defaultLetterSpacing,
		fontStyle,
	});

	const top = Math.round(yOnCanvas - textDimensions.height / 2);
	const left =
		align === 'center'
			? Math.round(xOnCanvas - textDimensions.width / 2)
			: Math.round(xOnCanvas);

	return {
		id,
		durationInFrames: TEXT_DURATION_IN_FRAMES,
		from,
		type: 'text',
		text,
		color: '#ffffff',
		top,
		left,
		width: textDimensions.width,
		height: textDimensions.height,
		align: align,
		opacity: 1,
		rotation: 0,
		fontFamily: defaultFontFamily,
		fontSize: DEFAULT_FONT_SIZE,
		lineHeight: defaultLineHeight,
		letterSpacing: defaultLetterSpacing,
		resizeOnEdit: true,
		direction: stringSeemsRightToLeft(text) ? 'rtl' : 'ltr',
		fontStyle,
		isDraggingInTimeline: false,
		strokeWidth: 0,
		strokeColor: '#000000',
		fadeInDurationInSeconds: 0,
		fadeOutDurationInSeconds: 0,
		background: null,
	};
};
