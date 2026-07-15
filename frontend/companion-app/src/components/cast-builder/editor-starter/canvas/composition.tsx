import {PlayerRef} from '@remotion/player';
import {useContext} from 'react';
import {AbsoluteFill, getRemotionEnvironment} from 'remotion';
import {EditModeContext} from '../edit-mode';
import {SortedOutlines} from '../selection-border/sorted-outlines';
import {useActiveCanvasSnapPoints} from '../utils/use-context';
import {isTimelineEmpty} from '../utils/is-timeline-empty';
import {useTracks} from '../utils/use-context';
import {EmptyCanvasPlaceholder} from './empty-canvas-placeholder';
import {Layers} from './layers';
import {CanvasSnapIndicators} from './snap/canvas-snap-indicators';
import {SolidDrawingTool} from './solid-drawing-tool';
import {TextInsertionTool} from './text-insertion-tool';

export type MainCompositionProps = {
	playerRef: React.RefObject<PlayerRef | null> | null;
};

export const MainComposition: React.FC<MainCompositionProps> = ({
	playerRef,
}) => {
	const {editMode} = useContext(EditModeContext);
	const {tracks} = useTracks();
	const {activeCanvasSnapPoints} = useActiveCanvasSnapPoints();

	return (
		<AbsoluteFill>
			<Layers tracks={tracks} />
			{getRemotionEnvironment().isPlayer ? <SortedOutlines /> : null}
			{getRemotionEnvironment().isPlayer ? (
				<CanvasSnapIndicators activeSnapPoints={activeCanvasSnapPoints} />
			) : null}
			{getRemotionEnvironment().isPlayer && isTimelineEmpty(tracks) ? (
				<EmptyCanvasPlaceholder />
			) : null}
			{editMode === 'draw-solid' && playerRef ? (
				<SolidDrawingTool playerRef={playerRef} />
			) : null}
			{editMode === 'create-text' && playerRef ? (
				<TextInsertionTool playerRef={playerRef} />
			) : null}
		</AbsoluteFill>
	);
};
