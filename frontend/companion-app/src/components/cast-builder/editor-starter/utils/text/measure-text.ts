import {measureText} from '@remotion/layout-utils';
import {turnFontStyleIntoCss} from '../../inspector/controls/font-style-controls/font-style-controls';
import {FontStyle} from '../../items/text/text-item-type';

export const getTextDimensions = ({
	text,
	fontFamily,
	fontSize,
	lineHeight,
	letterSpacing,
	fontStyle,
}: {
	text: string;
	fontFamily: string;
	fontSize: number;
	lineHeight: number;
	letterSpacing: number;
	fontStyle: FontStyle;
}): {width: number; height: number} => {
	const {width} = measureText({
		text,
		fontSize,
		fontFamily,
		fontWeight: fontStyle.weight,
		additionalStyles: {
			lineHeight: String(lineHeight),
			display: 'inline',
			whiteSpace: 'pre',
			letterSpacing: `${letterSpacing}px`,
			...turnFontStyleIntoCss(fontStyle),
		},
	});

	return {
		width: Math.ceil(width),
		height: Math.round(lineHeight * fontSize * text.split('\n').length),
	};
};
