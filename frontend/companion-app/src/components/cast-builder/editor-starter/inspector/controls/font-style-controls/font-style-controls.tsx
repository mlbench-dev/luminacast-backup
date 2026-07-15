import React, {memo, useCallback, useEffect, useState} from 'react';
import {FontStyle} from '../../../items/text/text-item-type';
import {
	Select,
	SelectContent,
	SelectSeparator,
	SelectTrigger,
	SelectValue,
} from '../../../select';
import {changeItem} from '../../../state/actions/change-item';
import {editAndRelayoutText} from '../../../state/actions/edit-and-relayout-text';
import {setTextItemHoverPreview} from '../../../state/actions/set-hover-preview';
import {
	getFontVariants,
	loadFontFromTextItem,
	loadFontInfoFromApi,
} from '../../../utils/text/load-font-from-text-item';
import {useWriteContext} from '../../../utils/use-context';
import {
	FontStyleSelectionItem,
	serializeFontStyle,
} from './font-style-selection-item';

export const turnFontStyleIntoCss = (
	fontStyle: FontStyle,
): React.CSSProperties => {
	return {
		...(fontStyle.variant.toLowerCase().includes('italic')
			? {fontStyle: 'italic'}
			: {}),
		fontWeight: fontStyle.weight,
	};
};

const FontStyleControlsUnmemoized: React.FC<{
	fontFamily: string;
	fontStyle: FontStyle;
	itemId: string;
}> = ({fontFamily, fontStyle, itemId}) => {
	const {setState} = useWriteContext();
	const [variants, setVariants] = useState<FontStyle[][] | null>([]);

	useEffect(() => {
		setVariants(null);
		loadFontInfoFromApi(fontFamily)
			.then((infos) => {
				const loadedVariants = getFontVariants(infos);
				setVariants(loadedVariants);
			})
			// eslint-disable-next-line no-console
			.catch(console.error);
	}, [fontFamily]);

	const applyFontStyle = useCallback(
		(value: string) => {
			const [variant, weight] = value.split('-');

			setState({
				update: (state) => {
					const newState = changeItem(state, itemId, (i) => {
						if (i.type === 'text') {
							return editAndRelayoutText(i, () => {
								if (
									i.fontStyle.variant === variant &&
									i.fontStyle.weight === weight
								) {
									return i;
								}

								return {
									...i,
									fontStyle: {
										variant,
										weight,
									},
								};
							});
						}
						if (i.type === 'captions') {
							return {
								...i,
								fontStyle: {
									variant,
									weight,
								},
							};
						}

						throw new Error(
							`Font style can only be changed for text and captions items`,
						);
					});

					return setTextItemHoverPreview({
						state: newState,
						hoverPreview: null,
					});
				},
				commitToUndoStack: true,
			});
		},
		[setState, itemId],
	);

	const onValueChange = useCallback(
		async (value: string) => {
			const [variant, weight] = value.split('-');
			await loadFontFromTextItem({
				fontFamily: fontFamily,
				fontVariant: variant,
				fontWeight: weight,
				fontInfosDuringRendering: null,
			});
			applyFontStyle(value);
		},
		[applyFontStyle, fontFamily],
	);

	const onOpenChange = useCallback(
		(open: boolean) => {
			if (!open) {
				applyFontStyle(serializeFontStyle(fontStyle));
			}
		},
		[applyFontStyle, fontStyle],
	);

	const resetFontStyle = useCallback(() => {
		setState({
			update: (state) => {
				return setTextItemHoverPreview({
					state,
					hoverPreview: null,
				});
			},
			commitToUndoStack: false,
		});
	}, [setState]);

	const previewFontStyle = useCallback(
		(newFontStyle: FontStyle) => {
			setState({
				update: (state) => {
					return {
						...state,
						textItemHoverPreview: {
							itemId,
							type: 'font-style',
							fontStyle: newFontStyle,
						},
					};
				},
				commitToUndoStack: false,
			});
		},
		[setState, itemId],
	);

	if (!variants) {
		return null;
	}

	return (
		<div className="mt-2 flex flex-col">
			<Select
				value={serializeFontStyle(fontStyle)}
				onValueChange={onValueChange}
				onOpenChange={onOpenChange}
			>
				<SelectTrigger className="w-full">
					<SelectValue placeholder="Font Style" />
				</SelectTrigger>
				<SelectContent className="w-full">
					{variants.map((variantGroup, i) => (
						<React.Fragment key={i}>
							{variantGroup.map((variant) => (
								<FontStyleSelectionItem
									key={serializeFontStyle(variant)}
									variant={variant}
									fontFamily={fontFamily}
									applyFontStyle={applyFontStyle}
									previewFontStyle={previewFontStyle}
									resetFontStyle={resetFontStyle}
								/>
							))}
							{i < variants.length - 1 && <SelectSeparator />}
						</React.Fragment>
					))}
				</SelectContent>
			</Select>
		</div>
	);
};

export const FontStyleControls = memo(FontStyleControlsUnmemoized);
