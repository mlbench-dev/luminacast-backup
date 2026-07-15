import {PlayerRef} from '@remotion/player';
import {useContext, useMemo} from 'react';
import {StateInitializedContext} from '../context-provider';
import {DropHandler} from '../drop-handler';
import {PreviewSizeContext} from '../preview-size';
import {useSafeZones} from '../safe-zone-context';
import {calculateCanvasTransformation} from '../utils/calculate-canvas-transformation';
import {CanvasTransformationContext} from '../utils/canvas-transformation-context';
import {Rect} from '../utils/fit-element-size-in-container';
import {EmptyCanvasContextMenuTrigger} from './empty-canvas-context-menu-trigger';
import {RemotionPlayer} from './player';
import {SafeZoneOverlay} from './SafeZoneOverlay';

const containerStyle = (options: {
	scale: number;
	xCorrection: number;
	yCorrection: number;
	width: number;
	height: number;
}): React.CSSProperties => {
	return {
		transform: `scale(${options.scale})`,
		marginLeft: options.xCorrection,
		marginTop: options.yCorrection,
		width: options.width,
		height: options.height,
		display: 'flex',
		position: 'absolute',
	};
};

export const ScaledPlayer = ({
	compositionHeight,
	compositionWidth,
	loop,
	playerRef,
	fps,
	canvasSize,
	framesShownInTimeline,
	canvasRef,
}: {
	compositionHeight: number;
	compositionWidth: number;
	loop: boolean;
	playerRef: React.RefObject<PlayerRef | null>;
	fps: number;
	canvasSize: Rect | null;
	framesShownInTimeline: number;
	canvasRef: React.RefObject<HTMLDivElement | null>;
}) => {
	const {size: previewSize} = useContext(PreviewSizeContext);
	const {activePlatforms} = useSafeZones();

	const canvasTransformation = useMemo(() => {
		if (!canvasSize) {
			return {
				scale: 1,
				xCorrection: 0,
				yCorrection: 0,
				centerX: 0,
				centerY: 0,
			};
		}

		return calculateCanvasTransformation({
			canvasSize,
			compositionHeight: compositionHeight,
			compositionWidth: compositionWidth,
			previewSize: previewSize.size,
		});
	}, [canvasSize, compositionHeight, compositionWidth, previewSize.size]);

	const hasCanvasSize = useMemo(() => {
		return !!canvasSize;
	}, [canvasSize]);

	const initialized = useContext(StateInitializedContext);

	const outer: React.CSSProperties = useMemo(() => {
		return {
			width: compositionWidth * canvasTransformation.scale,
			height: compositionHeight * canvasTransformation.scale,
			display: 'flex',
			flexDirection: 'column',
			position: 'absolute',
			left: canvasTransformation.centerX - previewSize.translation.x,
			top: canvasTransformation.centerY - previewSize.translation.y,
			justifyContent: 'flex-start',
			alignItems: 'normal',
			backgroundColor: 'black',
			opacity: hasCanvasSize && initialized ? 1 : 0,
		};
	}, [
		canvasTransformation,
		compositionHeight,
		compositionWidth,
		previewSize,
		hasCanvasSize,
		initialized,
	]);

	const style = useMemo(() => {
		return containerStyle({
			scale: canvasTransformation.scale,
			xCorrection: canvasTransformation.xCorrection,
			yCorrection: canvasTransformation.yCorrection,
			width: compositionWidth,
			height: compositionHeight,
		});
	}, [canvasTransformation, compositionWidth, compositionHeight]);

	return (
		<EmptyCanvasContextMenuTrigger
			playerRef={playerRef}
			canvasTransformation={canvasTransformation}
			previewSize={previewSize}
			canvasRef={canvasRef}
		>
			<DropHandler
				playerRef={playerRef}
				compositionHeight={compositionHeight}
				compositionWidth={compositionWidth}
				context="timeline"
			>
				<CanvasTransformationContext.Provider value={canvasTransformation}>
					<div style={outer}>
						<div style={style}>
							<RemotionPlayer
								compositionHeight={compositionHeight}
								compositionWidth={compositionWidth}
								loop={loop}
								playerRef={playerRef}
								durationInFrames={framesShownInTimeline}
								fps={fps}
							/>
							<SafeZoneOverlay
								activePlatforms={activePlatforms}
								compositionWidth={compositionWidth}
								compositionHeight={compositionHeight}
							/>
						</div>
					</div>
				</CanvasTransformationContext.Provider>
			</DropHandler>
		</EmptyCanvasContextMenuTrigger>
	);
};
