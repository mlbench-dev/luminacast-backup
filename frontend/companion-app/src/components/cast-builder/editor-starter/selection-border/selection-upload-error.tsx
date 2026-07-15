import React, {useMemo} from 'react';
import {useCanvasTransformationScale} from '../utils/canvas-transformation-context';

export const SelectionError: React.FC<{
	uploadError: Error;
	onRetry: undefined | (() => void);
	canRetry: boolean;
}> = ({uploadError, onRetry, canRetry}) => {
	const scale = useCanvasTransformationScale();

	const innerStyle: React.CSSProperties = useMemo(() => {
		return {
			fontSize: 11 / scale,
			lineHeight: `${15 / scale}px`,
		};
	}, [scale]);

	const padding = 8 / scale;

	return (
		<div
			className="absolute top-0 right-0 bottom-0 left-0 flex items-start justify-start text-white"
			style={innerStyle}
			title={uploadError.message}
		>
			<div
				style={{
					padding,
				}}
				className="flex flex-row items-center gap-2 bg-red-500"
			>
				{canRetry && onRetry ? (
					<button
						aria-label="Retry"
						onClick={(e) => {
							e.stopPropagation();
							onRetry();
						}}
					>
						Retry (Failed to upload)
					</button>
				) : (
					<span>Failed to upload</span>
				)}
			</div>
		</div>
	);
};
