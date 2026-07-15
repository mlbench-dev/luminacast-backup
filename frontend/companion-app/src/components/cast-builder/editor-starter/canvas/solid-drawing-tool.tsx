import {PlayerRef} from '@remotion/player';
import {useCallback, useContext} from 'react';
import {AbsoluteFill, useVideoConfig} from 'remotion';
import {EditModeContext} from '../edit-mode';
import {PreviewSizeContext} from '../preview-size';
import {addItem} from '../state/actions/add-item';
import {changeItem} from '../state/actions/change-item';
import {byDefaultKeepAspectRatioMap} from '../utils/aspect-ratio';
import {calculateScale} from '../utils/calculate-canvas-transformation';
import {generateRandomId} from '../utils/generate-random-id';
import {useWriteContext} from '../utils/use-context';
import {CanvasSizeContext} from './canvas-size';

const SHAPE_DURATION_IN_FRAMES = 100;

export const SolidDrawingTool: React.FC<{
	playerRef: React.RefObject<PlayerRef | null>;
}> = ({playerRef}) => {
	const {setState} = useWriteContext();
	const {width, height} = useVideoConfig();
	const {size} = useContext(PreviewSizeContext);
	const {setEditMode} = useContext(EditModeContext);
	const canvasSize = useContext(CanvasSizeContext);

	const onPointerDown = useCallback(
		(event: React.PointerEvent<HTMLDivElement>) => {
			event.preventDefault();
			event.stopPropagation();

			if (!canvasSize) {
				throw new Error('Could not find canvas size');
			}

			const scale = calculateScale({
				canvasSize: canvasSize,
				compositionWidth: width,
				compositionHeight: height,
				previewSize: size.size,
			});

			const container = playerRef.current?.getContainerNode();
			if (!container) {
				throw new Error('Could not find container');
			}
			const containerLeft = container.getBoundingClientRect().left;
			const containerTop = container.getBoundingClientRect().top;

			const xOnCanvas = (event.clientX - containerLeft) / scale;
			const yOnCanvas = (event.clientY - containerTop) / scale;

			const id = generateRandomId();

			const top = yOnCanvas;
			const left = xOnCanvas;

			let added = false;
			let wasModified = false;
			const addIfNotYetAdded = () => {
				if (added) {
					return;
				}
				setState({
					update: (state) => {
						return addItem({
							state,
							item: {
								type: 'solid',
								color: '#ffffff',
								durationInFrames: SHAPE_DURATION_IN_FRAMES,
								from: playerRef.current?.getCurrentFrame() ?? 0,
								top: Math.round(top),
								left: Math.round(left),
								width: 100,
								height: 100,
								isDraggingInTimeline: false,
								id,
								opacity: 1,
								borderRadius: 0,
								rotation: 0,
								keepAspectRatio: byDefaultKeepAspectRatioMap.solid,
								fadeInDurationInSeconds: 0,
								fadeOutDurationInSeconds: 0,
							},
							select: true,
							position: {type: 'front'},
						});
					},
					commitToUndoStack: true,
				});

				added = true;
			};

			const onPointerMove = (evt: PointerEvent) => {
				addIfNotYetAdded();
				wasModified = true;

				const containerNode = playerRef.current?.getContainerNode();
				if (!containerNode) {
					throw new Error('Could not find container');
				}
				const newContainerLeft = containerNode.getBoundingClientRect().left;
				const newContainerTop = containerNode.getBoundingClientRect().top;
				let newWidth = Math.round(
					(evt.clientX - newContainerLeft) / scale - xOnCanvas,
				);
				let newHeight = Math.round(
					(evt.clientY - newContainerTop) / scale - yOnCanvas,
				);

				setState({
					update: (state) => {
						return changeItem(state, id, (item) => {
							let leftToApply = left;
							let topToApply = top;
							if (newWidth < 0) {
								leftToApply = left + newWidth;
							} else if (newWidth === 0) {
								newWidth = 1;
							}
							if (newHeight < 0) {
								topToApply = top + newHeight;
							} else if (newHeight === 0) {
								newHeight = 1;
							}

							return {
								...item,
								left: Math.round(leftToApply),
								top: Math.round(topToApply),
								width: Math.abs(newWidth),
								height: Math.abs(newHeight),
							};
						});
					},
					commitToUndoStack: false,
				});
			};

			const onPointerUp = () => {
				addIfNotYetAdded();

				if (wasModified) {
					setState({
						update: (state) => state,
						commitToUndoStack: true,
					});
				}

				setEditMode('select');

				window.removeEventListener('pointermove', onPointerMove);
				window.removeEventListener('pointerup', onPointerUp);
			};

			window.addEventListener('pointermove', onPointerMove);
			window.addEventListener('pointerup', onPointerUp);

			return () => {
				window.removeEventListener('pointermove', onPointerMove);
				window.removeEventListener('pointerup', onPointerUp);
			};
		},
		[canvasSize, setState, height, playerRef, setEditMode, size.size, width],
	);

	return <AbsoluteFill onPointerDown={onPointerDown}></AbsoluteFill>;
};
