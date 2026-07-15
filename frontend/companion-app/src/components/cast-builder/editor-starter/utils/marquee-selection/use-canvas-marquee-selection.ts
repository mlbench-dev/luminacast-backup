import {PlayerRef} from '@remotion/player';
import {useCallback, useMemo, useState} from 'react';
import {PreviewSize} from 'remotion';
import {FEATURE_CANVAS_MARQUEE_SELECTION} from '../../flags';
import {MarqueeSelection} from '../../marquee-selection';
import {setSelectedItems} from '../../state/actions/set-selected-items';
import {unselectItemsOrDisableCropUI} from '../../state/actions/unselect-items';
import {CanvasTransformation} from '../calculate-canvas-transformation';
import {getRectAfterCrop} from '../get-dimensions-after-crop';
import {isLeftClick} from '../is-left-click';
import {useCurrentStateAsRef, useWriteContext} from '../use-context';
import {isItemInMarquee} from './is-item-in-marquee';

export const useCanvasMarqueeSelection = ({
	playerRef,
	canvasTransformation,
	previewSize,
	canvasRef,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
	canvasTransformation: CanvasTransformation;
	previewSize: PreviewSize;
	canvasRef: React.RefObject<HTMLDivElement | null>;
}) => {
	const [rect, setRect] = useState<MarqueeSelection | null>(null);
	const {setState} = useWriteContext();
	const state = useCurrentStateAsRef();

	const canvasLeft = canvasTransformation.centerX - previewSize.translation.x;
	const canvasTop = canvasTransformation.centerY - previewSize.translation.y;

	const onPointerDown: React.MouseEventHandler<HTMLDivElement> = useCallback(
		(initialPointerEvent) => {
			if (!isLeftClick(initialPointerEvent)) {
				return;
			}

			setState({
				update: unselectItemsOrDisableCropUI,
				commitToUndoStack: false,
			});

			if (!FEATURE_CANVAS_MARQUEE_SELECTION) {
				return;
			}

			const canvasRect = canvasRef.current?.getBoundingClientRect();
			if (!canvasRect) {
				throw new Error('need canvasrect');
			}

			const startX = initialPointerEvent.clientX - canvasRect.left;
			const startY = initialPointerEvent.clientY - canvasRect.top;

			const onPointerMove = (e: PointerEvent) => {
				const endX = e.clientX - canvasRect.left;
				const endY = e.clientY - canvasRect.top;

				const topLeftPoint = {
					x: Math.min(startX, endX),
					y: Math.min(startY, endY),
				};

				const bottomRightPoint = {
					x: Math.max(startX, endX),
					y: Math.max(startY, endY),
				};

				const rectToSet: MarqueeSelection = {
					start: topLeftPoint,
					end: bottomRightPoint,
				};

				setRect(rectToSet);

				const currentTime = playerRef.current?.getCurrentFrame() ?? null;
				if (currentTime === null) {
					throw new Error('need current time');
				}

				const selectedIds: string[] = [];

				const {items, tracks} = state.current.undoableState;

				for (const track of tracks) {
					if (track.hidden) {
						continue;
					}
					for (const itemId of track.items) {
						const item = items[itemId];
						if (item.from > currentTime) {
							continue;
						}

						if (item.from + item.durationInFrames < currentTime) {
							continue;
						}
						const rectAfterCrop = getRectAfterCrop(item);

						const itemX =
							canvasLeft + rectAfterCrop.left * canvasTransformation.scale;
						const itemY =
							canvasTop + rectAfterCrop.top * canvasTransformation.scale;
						const itemEndX =
							rectAfterCrop.width * canvasTransformation.scale + itemX;
						const itemEndY =
							rectAfterCrop.height * canvasTransformation.scale + itemY;

						if (
							isItemInMarquee({
								marquee: rectToSet,
								itemX,
								itemY,
								itemEndX,
								itemEndY,
							})
						) {
							selectedIds.push(item.id);
						}
					}
				}

				setState({
					update: (prev) => setSelectedItems(prev, selectedIds),
					commitToUndoStack: false,
				});
			};

			const onPointerUp = () => {
				cleanup();
			};

			const cleanup = () => {
				window.removeEventListener('pointermove', onPointerMove);
				window.removeEventListener('pointerup', onPointerUp);
				setRect(null);
			};

			window.addEventListener('pointermove', onPointerMove);
			window.addEventListener('pointerup', onPointerUp);
		},
		[
			canvasLeft,
			canvasRef,
			canvasTop,
			canvasTransformation.scale,
			playerRef,
			setState,
			state,
		],
	);

	return useMemo(
		() => ({
			rect,
			onPointerDown,
		}),
		[onPointerDown, rect],
	);
};
