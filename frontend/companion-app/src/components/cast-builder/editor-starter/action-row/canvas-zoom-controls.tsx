import {useContext} from 'react';
import {canvasRef} from '../canvas/canvas';
import {ResetZoomIcon} from '../icons/reset-zoom';
import {ZoomInIcon} from '../icons/zoom-in';
import {ZoomOutIcon} from '../icons/zoom-out';
import {PreviewSizeContext} from '../preview-size';
import {calculateScale} from '../utils/calculate-canvas-transformation';
import {MAX_CANVAS_ZOOM, MIN_CANVAS_ZOOM} from '../utils/smooth-zoom';
import {useDimensions} from '../utils/use-context';

export const CanvasZoomControls: React.FC = () => {
	const {size, setSize} = useContext(PreviewSizeContext);
	const {compositionWidth, compositionHeight} = useDimensions();

	const isFitMode = size.size === 'auto';
	const currentZoom = typeof size.size === 'number' ? size.size : 1;

	// Calculate if buttons should be disabled
	const isZoomInDisabled = !isFitMode && currentZoom >= MAX_CANVAS_ZOOM;
	const isZoomOutDisabled = !isFitMode && currentZoom <= MIN_CANVAS_ZOOM;

	const handleZoomIn = () => {
		if (isZoomInDisabled) return;

		setSize((oldSize) => {
			if (oldSize.size === 'auto') {
				const oldScale = calculateScale({
					canvasSize: canvasRef.current!.getBoundingClientRect(),
					compositionWidth: compositionWidth,
					compositionHeight: compositionHeight,
					previewSize: 'auto',
				});

				return {
					...oldSize,
					size: oldScale * 1.2,
				};
			}
			return {
				...oldSize,
				size: Math.min(oldSize.size * 1.2, MAX_CANVAS_ZOOM),
			};
		});
	};

	const handleZoomOut = () => {
		if (isZoomOutDisabled) return;

		setSize((oldSize) => {
			const oldScale = calculateScale({
				canvasSize: canvasRef.current!.getBoundingClientRect(),
				compositionWidth: compositionWidth,
				compositionHeight: compositionHeight,
				previewSize: 'auto',
			});
			if (oldSize.size === 'auto') {
				return {
					...oldSize,
					size: oldScale * 0.8,
				};
			}
			return {
				...oldSize,
				size: Math.max(oldSize.size * 0.8, MIN_CANVAS_ZOOM),
			};
		});
	};

	const handleFit = () => {
		setSize(() => ({
			translation: {
				x: 0,
				y: 0,
			},
			size: 'auto',
		}));
	};

	return (
		<div className="flex h-full items-center rounded bg-white/5 text-white">
			{!isFitMode && (
				<button
					onClick={handleFit}
					data-is-fit-mode={isFitMode}
					className="editor-starter-focus-ring border-editor-starter-panel flex h-full w-8 items-center justify-center border-r text-sm hover:bg-white/5"
					title="Reset to Auto"
					aria-label="Reset to Auto"
				>
					<ResetZoomIcon />
				</button>
			)}
			<button
				onClick={handleZoomOut}
				disabled={isZoomOutDisabled}
				className={`border-editor-starter-panel editor-starter-focus-ring flex h-full w-8 items-center justify-center border-r transition-colors ${
					isZoomOutDisabled ? 'cursor-default' : 'hover:bg-white/5'
				}`}
				title="Zoom out"
				aria-label="Zoom out"
			>
				<ZoomOutIcon
					data-disabled={isZoomOutDisabled}
					className="h-3 w-3 data-[disabled=true]:opacity-50"
				/>
			</button>

			<div className="border-editor-starter-panel flex h-full min-w-[60px] cursor-default items-center justify-center border-r text-center text-xs font-medium">
				{isFitMode ? 'Fit' : `${Math.round(currentZoom * 100)}%`}
			</div>

			<button
				onClick={handleZoomIn}
				disabled={isZoomInDisabled}
				className={`editor-starter-focus-ring flex h-full w-8 items-center justify-center ${
					isZoomInDisabled ? 'cursor-default' : 'hover:bg-white/5'
				}`}
				title="Zoom in"
				aria-label="Zoom in"
			>
				<ZoomInIcon
					data-disabled={isZoomInDisabled}
					className="h-3 w-3 data-[disabled=true]:opacity-50"
				/>
			</button>
		</div>
	);
};
