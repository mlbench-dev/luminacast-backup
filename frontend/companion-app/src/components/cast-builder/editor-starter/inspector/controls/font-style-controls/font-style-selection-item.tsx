import {useCallback, useRef} from 'react';
import {FEATURE_CHANGE_FONT_STYLE_ON_HOVER} from '../../../flags';
import {FontStyle} from '../../../items/text/text-item-type';
import {SelectItem} from '../../../select';
import {loadFontFromTextItem} from '../../../utils/text/load-font-from-text-item';

export const serializeFontStyle = (fontStyle: FontStyle) => {
	return `${fontStyle.variant}-${fontStyle.weight}`;
};

export const renderWeight = (weight: string) => {
	switch (weight) {
		case '100':
			return 'Thin';
		case '200':
			return 'Extra Light';
		case '300':
			return 'Light';
		case '400':
			return 'Regular';
		case '500':
			return 'Medium';
		case '600':
			return 'Semi Bold';
		case '700':
			return 'Bold';
		case '800':
			return 'Extra Bold';
		case '900':
			return 'Black';
		default:
			return weight;
	}
};

export const renderVariant = (variant: string) => {
	if (variant === 'normal') {
		return null;
	}
	if (variant === 'italic') {
		return 'Italic';
	}
	if (variant === 'oblique') {
		return 'Oblique';
	}
	return variant.charAt(0).toUpperCase() + variant.slice(1);
};

export const FontStyleSelectionItem: React.FC<{
	variant: FontStyle;
	fontFamily: string;
	applyFontStyle: (value: string) => void;
	previewFontStyle: (value: FontStyle) => void;
	resetFontStyle: () => void;
}> = ({
	variant,
	fontFamily,
	applyFontStyle,
	previewFontStyle,
	resetFontStyle,
}) => {
	const hovered = useRef(false);

	const onClick = useCallback(() => {
		applyFontStyle(serializeFontStyle(variant));
	}, [applyFontStyle, variant]);

	const onMouseEnter = useCallback(async () => {
		hovered.current = true;
		await loadFontFromTextItem({
			fontFamily,
			fontVariant: variant.variant,
			fontWeight: variant.weight,
			fontInfosDuringRendering: null,
		});
		if (!hovered.current) {
			return; // After loading, it is not hovered anymore
		}

		previewFontStyle(variant);
	}, [fontFamily, variant, previewFontStyle]);

	const onMouseLeave = useCallback(async () => {
		hovered.current = false;
		resetFontStyle();
	}, [resetFontStyle]);

	return (
		<SelectItem
			value={serializeFontStyle(variant)}
			onMouseEnter={
				FEATURE_CHANGE_FONT_STYLE_ON_HOVER ? onMouseEnter : undefined
			}
			onMouseLeave={
				FEATURE_CHANGE_FONT_STYLE_ON_HOVER ? onMouseLeave : undefined
			}
			onClick={onClick}
		>
			<span className="text-xs text-neutral-300">
				{[renderVariant(variant.variant), renderWeight(variant.weight)]
					.filter(Boolean)
					.join(' ')}
			</span>
		</SelectItem>
	);
};
