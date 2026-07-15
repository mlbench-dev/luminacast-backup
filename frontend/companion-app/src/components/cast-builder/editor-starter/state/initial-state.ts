import {
	DEFAULT_COMPOSITION_HEIGHT,
	DEFAULT_COMPOSITION_WIDTH,
	DEFAULT_FPS,
} from '../constants';
import {DEFAULT_LOOP} from './loop-persistance';
import {DEFAULT_SNAPPING_ENABLED} from './snapping-persistance';
import {DEFAULT_TIMELINE_HEIGHT} from './timeline-height-persistance';
import {EditorState, TrackType, UndoableState} from './types';

/**
 * This is the initial state of the timeline.
 * It's here to show you how to initialize the timeline with data.
 * You can change the shape of the data to fit your needs.
 */
const defaultInitialTracks: TrackType[] = [];

const defaultInitialState: UndoableState = {
	tracks: defaultInitialTracks,
	fps: DEFAULT_FPS,
	compositionWidth: DEFAULT_COMPOSITION_WIDTH,
	compositionHeight: DEFAULT_COMPOSITION_HEIGHT,
	items: {},
	assets: {},
	deletedAssets: [],
};

export const getInitialState = (): EditorState => {
	return {
		selectedItems: [],
		itemSelectedForCrop: null,
		undoableState: defaultInitialState,
		textItemEditing: null,
		textItemHoverPreview: null,
		renderingTasks: [],
		captioningTasks: [],
		initialized: false,
		itemsBeingTrimmed: [],
		loop: DEFAULT_LOOP,
		assetStatus: {},
		timelineHeight: DEFAULT_TIMELINE_HEIGHT,
		isSnappingEnabled: DEFAULT_SNAPPING_ENABLED,
		activeSnapPoint: null,
		activeCanvasSnapPoints: [],
	};
};
