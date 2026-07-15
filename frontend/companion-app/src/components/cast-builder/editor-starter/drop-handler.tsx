import {PlayerRef} from '@remotion/player';
import React, {useCallback, useContext, useMemo} from 'react';
import {AbsoluteFill} from 'remotion';
import {addAsset, DropPosition} from './assets/add-asset';
import {CANVAS_PADDING} from './canvas/canvas';
import {CanvasSizeContext} from './canvas/canvas-size';
import {
	FEATURE_DROP_ASSETS_ON_CANVAS,
	FEATURE_DROP_ASSETS_ON_TIMELINE,
} from './flags';
import {PreviewSizeContext} from './preview-size';
import {calculateScale} from './utils/calculate-canvas-transformation';
import {useCurrentStateAsRef, useWriteContext} from './utils/use-context';

export const DropHandler: React.FC<{
	children: React.ReactNode;
	playerRef: React.RefObject<PlayerRef | null>;
	compositionHeight: number;
	compositionWidth: number;
	context: 'canvas' | 'timeline';
}> = ({children, playerRef, compositionHeight, compositionWidth, context}) => {
	const size = useContext(CanvasSizeContext);
	const {size: previewSize} = useContext(PreviewSizeContext);
	const timelineWriteContext = useWriteContext();
	const stateAsRef = useCurrentStateAsRef();

	const onDragOver: React.DragEventHandler = useCallback(
		(e) => {
			if (!FEATURE_DROP_ASSETS_ON_TIMELINE && context === 'timeline') {
				return;
			}

			if (!FEATURE_DROP_ASSETS_ON_CANVAS && context === 'canvas') {
				return;
			}
			e.preventDefault();
			e.stopPropagation();
			e.dataTransfer.dropEffect = 'copy';
		},
		[context],
	);

	const onDrop: React.DragEventHandler = useCallback(
		async (e) => {
			if (!FEATURE_DROP_ASSETS_ON_TIMELINE && context === 'timeline') {
				return;
			}

			if (!FEATURE_DROP_ASSETS_ON_CANVAS && context === 'canvas') {
				return;
			}
			e.preventDefault();

			const {current} = playerRef;
			if (!current) {
				throw new Error('playerRef is null');
			}

			let dropPosition: DropPosition | null = null;
			if (context === 'canvas') {
				const containerNode = current.getContainerNode();
				if (!containerNode) {
					throw new Error('containerNode is null');
				}

				const playerRect = containerNode.getBoundingClientRect();
				if (!size) {
					throw new Error('size is null');
				}

				const scale = calculateScale({
					canvasSize: size,
					compositionHeight,
					compositionWidth,
					previewSize: previewSize.size,
				});
				const dropPositionX = Math.round((e.clientX - playerRect.left) / scale);
				const dropPositionY = Math.round((e.clientY - playerRect.top) / scale);
				dropPosition = {x: dropPositionX, y: dropPositionY};
			} else {
				dropPosition = null;
			}

			const state = stateAsRef.current;
			const tracks = state.undoableState.tracks;
			const fps = state.undoableState.fps;

			const uploadPromises = [];
			for (const file of e.dataTransfer.files) {
				uploadPromises.push(
					addAsset({
						file,
						compositionHeight,
						compositionWidth,
						tracks: tracks,
						fps,
						timelineWriteContext: timelineWriteContext,
						playerRef,
						dropPosition,
						filename: file.name,
					}),
				);
			}
			await Promise.all(uploadPromises);

			e.dataTransfer.clearData();
		},
		[
			compositionHeight,
			compositionWidth,
			context,
			playerRef,
			previewSize.size,
			size,
			timelineWriteContext,
			stateAsRef,
		],
	);

	const style = useMemo(() => {
		if (context === 'canvas') {
			return {
				padding: CANVAS_PADDING,
			};
		}

		return {};
	}, [context]);

	return (
		<AbsoluteFill onDragOver={onDragOver} onDrop={onDrop} style={style}>
			{children}
		</AbsoluteFill>
	);
};
