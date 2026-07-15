import {PlayerRef} from '@remotion/player';
import React, {useCallback, useContext, useEffect} from 'react';
import {EditModeContext} from '../edit-mode';
import {
	FEATURE_CANVAS_ZOOM_GESTURES,
	FEATURE_CANVAS_ZOOM_KEYBOARD_SHORTCUTS,
} from '../flags';
import {PreviewSizeContext} from '../preview-size';
import {calculateScale} from '../utils/calculate-canvas-transformation';
import {clsx} from '../utils/clsx';
import {
	getCenterPointWhileScrolling,
	getEffectiveTranslation,
} from '../utils/get-effective-translation';
import {
	MAX_CANVAS_ZOOM,
	MIN_CANVAS_ZOOM,
	smoothenZoom,
	unsmoothenZoom,
} from '../utils/smooth-zoom';
import {useDimensions, useFps, useTimelineContext} from '../utils/use-context';
import {useElementSize} from '../utils/use-element-size';
import {useKeybinding} from '../utils/use-keybindings';
import {CanvasSizeProvider} from './canvas-size';
import {ScaledPlayer} from './scaled-player';

export const canvasRef = React.createRef<HTMLDivElement | null>();

export const CANVAS_PADDING = 16;
const ZOOM_PX_FACTOR = 0.003;

export const Canvas = ({
	playerRef,
	loop,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
	loop: boolean;
}) => {
	const {setSize, size: previewSize} = useContext(PreviewSizeContext);
	const isFit = previewSize.size === 'auto';

	const size = useElementSize(canvasRef, {
		triggerOnWindowResize: true,
	});

	const {durationInFrames} = useTimelineContext();
	const {compositionHeight, compositionWidth} = useDimensions();
	const {fps} = useFps();

	const onWheel = useCallback(
		(e: WheelEvent) => {
			if (!size) {
				return;
			}

			const wantsToZoom = e.ctrlKey || e.metaKey;

			if (!wantsToZoom && isFit) {
				return;
			}

			e.preventDefault();

			setSize((prevSize) => {
				const scale = calculateScale({
					canvasSize: size,
					compositionHeight,
					compositionWidth,
					previewSize: prevSize.size,
				});

				// Zoom in/out
				if (wantsToZoom) {
					const oldSize = prevSize.size === 'auto' ? scale : prevSize.size;
					const smoothened = smoothenZoom(oldSize);
					const added = smoothened + e.deltaY * ZOOM_PX_FACTOR;
					const unsmoothened = unsmoothenZoom(added);

					const {centerX, centerY} = getCenterPointWhileScrolling({
						size,
						clientX: e.clientX,
						clientY: e.clientY,
						compositionWidth,
						compositionHeight,
						scale,
						translation: prevSize.translation,
					});

					const zoomDifference = unsmoothened - oldSize;

					const uvCoordinatesX = centerX / compositionWidth;
					const uvCoordinatesY = centerY / compositionHeight;

					const correctionLeft =
						-uvCoordinatesX * (zoomDifference * compositionWidth) +
						(1 - uvCoordinatesX) * zoomDifference * compositionWidth;
					const correctionTop =
						-uvCoordinatesY * (zoomDifference * compositionHeight) +
						(1 - uvCoordinatesY) * zoomDifference * compositionHeight;

					return {
						translation: getEffectiveTranslation({
							translation: {
								x: prevSize.translation.x - correctionLeft / 2,
								y: prevSize.translation.y - correctionTop / 2,
							},
							canvasSize: size,
							compositionHeight,
							compositionWidth,
							scale,
						}),
						size: unsmoothened,
					};
				}

				const effectiveTranslation = getEffectiveTranslation({
					translation: prevSize.translation,
					canvasSize: size,
					compositionHeight,
					compositionWidth,
					scale,
				});

				// Pan
				return {
					...prevSize,
					translation: getEffectiveTranslation({
						translation: {
							x: effectiveTranslation.x + e.deltaX,
							y: effectiveTranslation.y + e.deltaY,
						},
						canvasSize: size,
						compositionHeight,
						compositionWidth,
						scale,
					}),
				};
			});
		},
		[compositionHeight, compositionWidth, isFit, setSize, size],
	);

	useEffect(() => {
		if (!FEATURE_CANVAS_ZOOM_GESTURES) {
			return;
		}

		const {current} = canvasRef;
		if (!current) {
			return;
		}

		current.addEventListener('wheel', onWheel, {passive: false});

		return () =>
			// @ts-expect-error - missing types
			current.removeEventListener('wheel', onWheel, {
				passive: false,
			});
	}, [onWheel]);

	const onReset = useCallback(() => {
		setSize(() => {
			return {
				translation: {
					x: 0,
					y: 0,
				},
				size: 'auto',
			};
		});
	}, [setSize]);

	const onZoomIn = useCallback(() => {
		if (!size) {
			return;
		}

		setSize((prevSize) => {
			const scale = calculateScale({
				canvasSize: size,
				compositionHeight,
				compositionWidth,
				previewSize: prevSize.size,
			});
			return {
				translation: {
					x: 0,
					y: 0,
				},
				size: Math.min(MAX_CANVAS_ZOOM, scale * 2),
			};
		});
	}, [compositionHeight, compositionWidth, setSize, size]);

	const onZoomOut = useCallback(() => {
		if (!size) {
			return;
		}

		setSize((prevSize) => {
			const scale = calculateScale({
				canvasSize: size,
				compositionHeight,
				compositionWidth,
				previewSize: prevSize.size,
			});
			return {
				translation: {
					x: 0,
					y: 0,
				},
				size: Math.max(MIN_CANVAS_ZOOM, scale / 2),
			};
		});
	}, [compositionHeight, compositionWidth, setSize, size]);

	const keybindings = useKeybinding();

	useEffect(() => {
		if (!FEATURE_CANVAS_ZOOM_KEYBOARD_SHORTCUTS) {
			return;
		}

		const resetBinding = keybindings.registerKeybinding({
			event: 'keydown',
			key: '0',
			commandCtrlKey: false,
			callback: onReset,
			preventDefault: true,
			triggerIfInputFieldFocused: false,
		});

		const zoomIn = keybindings.registerKeybinding({
			event: 'keydown',
			key: '+',
			commandCtrlKey: false,
			callback: onZoomIn,
			preventDefault: true,
			triggerIfInputFieldFocused: false,
		});

		const zoomOut = keybindings.registerKeybinding({
			event: 'keydown',
			key: '-',
			commandCtrlKey: false,
			callback: onZoomOut,
			preventDefault: true,
			triggerIfInputFieldFocused: false,
		});

		return () => {
			resetBinding.unregister();
			zoomIn.unregister();
			zoomOut.unregister();
		};
	}, [keybindings, onReset, onZoomIn, onZoomOut]);

	const {editMode} = useContext(EditModeContext);

	return (
		<div
			className={clsx(
				'bg-editor-starter-bg flex flex-1 overflow-hidden select-none',
				editMode === 'draw-solid' && 'cursor-crosshair',
				editMode === 'create-text' && 'cursor-text',
			)}
		>
			<div className="relative flex flex-1" ref={canvasRef}>
				<CanvasSizeProvider size={size}>
					<ScaledPlayer
						compositionHeight={compositionHeight}
						compositionWidth={compositionWidth}
						loop={loop}
						playerRef={playerRef}
						fps={fps}
						canvasSize={size}
						framesShownInTimeline={durationInFrames}
						canvasRef={canvasRef}
					/>
				</CanvasSizeProvider>
			</div>
		</div>
	);
};
