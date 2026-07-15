import {Pie} from '@remotion/shapes';
import React, {useMemo} from 'react';
import {AssetUploadProgress} from '../assets/assets';
import {useCanvasTransformationScale} from '../utils/canvas-transformation-context';
import {formatBytes} from '../utils/format-bytes';

export const SelectionUploadProgress: React.FC<{
	uploadProgress: AssetUploadProgress;
}> = ({uploadProgress}) => {
	const scale = useCanvasTransformationScale();

	const innerStyle: React.CSSProperties = useMemo(() => {
		return {
			fontSize: 11 / scale,
			lineHeight: `${15 / scale}px`,
		};
	}, [scale]);

	const iconSize = 11 / scale;
	const spacerWidth = 12 / scale;
	const padding = 8 / scale;

	const remainingBytes = uploadProgress.totalBytes - uploadProgress.loadedBytes;

	return (
		<div
			className="absolute top-0 right-0 bottom-0 left-0 flex items-start justify-start text-white"
			style={innerStyle}
		>
			<div
				style={{
					padding,
				}}
				className="bg-editor-starter-accent flex flex-row items-center justify-center"
			>
				<Pie
					radius={iconSize / 2.5}
					fill="white"
					progress={uploadProgress.progress}
				></Pie>
				<div
					style={{
						width: spacerWidth,
					}}
				></div>
				{remainingBytes === 0 ? 'Processing' : formatBytes(remainingBytes)}
			</div>
		</div>
	);
};
