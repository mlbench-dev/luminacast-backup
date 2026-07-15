import React, {useMemo} from 'react';
import {useCanvasTransformationScale} from '../utils/canvas-transformation-context';
import {getRectAfterCrop} from '../utils/get-dimensions-after-crop';
import {useItem} from '../utils/use-context';

export const SelectionDimensions: React.FC<{
	itemId: string;
}> = ({itemId}) => {
	const item = useItem(itemId);
	const rectAfterCrop = useMemo(() => getRectAfterCrop(item), [item]);

	const scale = useCanvasTransformationScale();

	const outerStyle: React.CSSProperties = useMemo(() => {
		return {
			marginBottom: -30 / scale,
		};
	}, [scale]);

	const innerStyle: React.CSSProperties = useMemo(() => {
		return {
			padding: 3 / scale,
			fontSize: 11 / scale,
			lineHeight: `${15 / scale}px`,
		};
	}, [scale]);

	return (
		<div
			className="pointer-events-none absolute bottom-0 flex w-full flex-row items-center justify-center"
			style={outerStyle}
		>
			<div
				className="bg-editor-starter-accent inline-block text-xs text-white"
				style={innerStyle}
			>
				{Math.floor(rectAfterCrop.width)}x{Math.floor(rectAfterCrop.height)}
			</div>
		</div>
	);
};
