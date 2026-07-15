import {fillTextBox, measureText} from '@remotion/layout-utils';
import {createRoundedTextBox} from '@remotion/rounded-text-box';
import React, {useMemo} from 'react';
import {turnFontStyleIntoCss} from '../../inspector/controls/font-style-controls/font-style-controls';
import {TextItem} from './text-item-type';

export const RoundedTextBox: React.FC<{
	textItem: TextItem;
	opacity: number;
}> = ({textItem, opacity}) => {
	if (!textItem.background) {
		throw new Error('Text item has no background');
	}

	const layout = useMemo(() => {
		const endLines = [''];
		const lines = textItem.text.split('\n');
		for (const line of lines) {
			const words = line.split(' ');
			const box = fillTextBox({
				maxBoxWidth: textItem.width,
				maxLines: 1000,
			});

			const add = (word: string) => {
				if (word === '') {
					return;
				}

				const {newLine} = box.add({
					fontFamily: textItem.fontFamily,
					fontSize: textItem.fontSize,
					fontWeight: textItem.fontStyle.weight,
					text: word,
					additionalStyles: {
						lineHeight: textItem.lineHeight,
						display: 'inline',
						whiteSpace: 'pre',
						letterSpacing: `${textItem.letterSpacing}px`,
						...turnFontStyleIntoCss(textItem.fontStyle),
					},
				});
				if (newLine) {
					endLines.push('');
				}
				// Would not add a trailing space in the end
				if (newLine && word === ' ') {
					return;
				}
				endLines[endLines.length - 1] += word;
			};

			for (const word of words) {
				add(word);
				add(' ');
			}
		}
		return endLines;
	}, [
		textItem.width,
		textItem.fontFamily,
		textItem.fontSize,
		textItem.fontStyle,
		textItem.letterSpacing,
		textItem.lineHeight,
		textItem.text,
	]);

	const textMeasurements = useMemo(() => {
		return layout.map((t) =>
			measureText({
				text: t,
				fontFamily: textItem.fontFamily,
				fontSize: textItem.fontSize,
				fontWeight: textItem.fontStyle.weight,
				additionalStyles: {
					lineHeight: textItem.lineHeight,
					display: 'inline',
					whiteSpace: 'pre',
					letterSpacing: `${textItem.letterSpacing}px`,
					...turnFontStyleIntoCss(textItem.fontStyle),
				},
			}),
		);
	}, [
		layout,
		textItem.fontFamily,
		textItem.fontSize,
		textItem.fontStyle,
		textItem.letterSpacing,
		textItem.lineHeight,
	]);

	const roundedTextBox = useMemo(() => {
		return createRoundedTextBox({
			textMeasurements,
			textAlign: textItem.align,
			horizontalPadding: textItem.background!.horizontalPadding,
			borderRadius: textItem.background!.borderRadius,
		});
	}, [textItem.align, textItem.background, textMeasurements]);

	const textWidth = useMemo(() => {
		return textMeasurements.reduce((acc, curr) => Math.max(acc, curr.width), 0);
	}, [textMeasurements]);

	// If the user resizes the text, the text box size might be different from the text width.
	const widthDifference = textItem.width - textWidth;
	const offsetLeft =
		textItem.align === 'center'
			? widthDifference / 2
			: textItem.align === 'right'
				? widthDifference
				: 0;

	return (
		<svg
			viewBox={roundedTextBox.boundingBox.viewBox}
			style={{
				position: 'absolute',
				left:
					textItem.left - textItem.background.horizontalPadding + offsetLeft,
				top: textItem.top,
				width: roundedTextBox.boundingBox.width,
				height: roundedTextBox.boundingBox.height,
				transform: `rotate(${textItem.rotation}deg)`,
				opacity,
			}}
		>
			<path d={roundedTextBox.d} fill={textItem.background.color} />
		</svg>
	);
};
