import {AssetState, EditorStarterAsset} from '../assets/assets';
import {CanvasSnapPoint} from '../canvas/snap/canvas-snap-types';
import {CaptioningTask} from '../captioning/caption-state';
import {EditorStarterItem} from '../items/item-type';
import {TextItemHoverPreview} from '../items/text/override-text-item-with-hover-preview';
import {ItemBeingTrimmed} from '../items/trim-indicator';
import {RenderingTask} from '../rendering/render-state';
import {SnapPoint} from '../timeline/utils/snap-points';

// The shape of the state is explained here:
// https://remotion.dev/docs/editor-starter/state-management

export type TrackType = {
	items: string[];
	id: string;
	hidden: boolean;
	muted: boolean;
};

type DeletedAsset = {
	remoteUrl: string | null;
	remoteFileKey: string | null;
	assetId: string;
	statusAtDeletion: AssetState;
};

// Undoable state: https://remotion.dev/docs/editor-starter/state-management#undoable-state
export type UndoableState = {
	tracks: TrackType[];
	assets: Record<string, EditorStarterAsset>;
	items: Record<string, EditorStarterItem>;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	deletedAssets: DeletedAsset[];
};

export type EditorState = {
	undoableState: UndoableState;
	selectedItems: string[];
	textItemEditing: string | null;
	textItemHoverPreview: TextItemHoverPreview | null;
	itemSelectedForCrop: string | null;
	renderingTasks: RenderingTask[];
	captioningTasks: CaptioningTask[];
	initialized: boolean;
	itemsBeingTrimmed: ItemBeingTrimmed[];
	loop: boolean;
	timelineHeight: number;
	assetStatus: Record<string, AssetState>;
	isSnappingEnabled: boolean;
	activeSnapPoint: SnapPoint | null;
	activeCanvasSnapPoints: CanvasSnapPoint[];
};
