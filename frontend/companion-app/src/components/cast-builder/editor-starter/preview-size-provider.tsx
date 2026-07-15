import React, {useCallback, useMemo, useState} from 'react';
import {PreviewSize, PreviewSizeContext, PreviewSizeCtx} from './preview-size';

export const PreviewSizeProvider: React.FC<{
	readonly children: React.ReactNode;
}> = ({children}) => {
	const [size, setSizeState] = useState<PreviewSize>(() => ({
		size: 'auto',
		translation: {
			x: 0,
			y: 0,
		},
	}));
	const [translation, setTranslation] = useState(() => {
		return {
			x: 0,
			y: 0,
		};
	});

	const setSize = useCallback(
		(newValue: (prevState: PreviewSize) => PreviewSize) => {
			setSizeState((prevState) => {
				const newVal = newValue(prevState);
				return newVal;
			});
		},
		[],
	);

	const previewSizeCtx: PreviewSizeCtx = useMemo(() => {
		return {
			size: size,
			setSize,
			translation,
			setTranslation,
		};
	}, [size, setSize, translation]);

	return (
		<PreviewSizeContext.Provider value={previewSizeCtx}>
			{children}
		</PreviewSizeContext.Provider>
	);
};
