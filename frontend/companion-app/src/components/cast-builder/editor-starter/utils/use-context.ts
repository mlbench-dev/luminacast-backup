import {useContext} from 'react';
import {EditorStarterAsset} from '../assets/assets';
import {getAssetFromItem} from '../assets/utils';
import {
	ActiveCanvasSnapContext,
	ActiveTimelineSnapContext,
	AllItemsContext,
	AssetsContext,
	AssetStatusContext,
	CanUseUndoStackContext,
	CaptionStateContext,
	CurrentStateContext,
	DimensionsContext,
	FpsContext,
	FullStateContext,
	LoopContext,
	RenderingContext,
	SelectedItemsContext,
	SnappingEnabledContext,
	TimelineContext,
	TimelineHeightContext,
	TracksContext,
	TimelineWriteOnlyContext as WriteOnlyContext,
} from '../context-provider';
import {EditorStarterItem} from '../items/item-type';
import {findAssetById} from './find-asset-by-id';

export const useTimelineContext = () => {
	const context = useContext(TimelineContext);
	if (!context) {
		throw new Error('TimelineContext is not set');
	}
	return context;
};

export const useFps = () => {
	const context = useContext(FpsContext);
	if (!context) {
		throw new Error('FpsContext is not set');
	}
	return context;
};

export const useLoop = () => {
	const context = useContext(LoopContext);
	return context;
};

export const useSnappingEnabled = () => {
	const context = useContext(SnappingEnabledContext);
	if (context === null) {
		throw new Error('TimelineSnappingEnabledContext is not set');
	}
	return context;
};

export const useTimelineHeight = () => {
	const context = useContext(TimelineHeightContext);
	if (!context) {
		throw new Error('TimelineHeightContext is not set');
	}
	return context;
};

export const useDimensions = () => {
	const context = useContext(DimensionsContext);
	if (!context) {
		throw new Error('DimensionsContext is not set');
	}
	return context;
};

export const useSelectedItems = () => {
	const context = useContext(SelectedItemsContext);
	if (!context) {
		throw new Error('SelectedItemsContext is not set');
	}
	return context;
};

export const useAssets = () => {
	const context = useContext(AssetsContext);
	if (!context) {
		throw new Error('AssetsContext is not set');
	}
	return context;
};

export const useAssetStatus = () => {
	const context = useContext(AssetStatusContext);
	if (!context) {
		throw new Error('AssetStatusContext is not set');
	}
	return context;
};

export const useTracks = () => {
	const context = useContext(TracksContext);
	if (!context) {
		throw new Error('TracksContext is not set');
	}
	return context;
};

export const useAllItems = () => {
	const context = useContext(AllItemsContext);
	if (!context) {
		throw new Error('AllItemsContext is not set');
	}
	return context;
};

export const useItem = (itemId: string) => {
	const item = useContext(AllItemsContext);
	if (!item) {
		throw new Error(`item ${itemId} not found`);
	}

	return item.items[itemId];
};

export const useCaptionState = () => {
	const context = useContext(CaptionStateContext);
	if (!context) {
		throw new Error('CaptionStateContext is not set');
	}
	return context;
};

export const useCurrentStateAsRef = () => {
	const context = useContext(CurrentStateContext);
	if (!context) {
		throw new Error('CurrentStateContext is not set');
	}
	return context.state;
};

export const useFullState = () => {
	const context = useContext(FullStateContext);
	if (!context) {
		throw new Error('FullStateContext is not set');
	}
	return context;
};

export const useWriteContext = () => {
	const context = useContext(WriteOnlyContext);
	if (!context) {
		throw new Error('WriteOnlyContext is not set');
	}
	return context;
};

export const useRendering = () => {
	const context = useContext(RenderingContext);
	if (!context) {
		throw new Error('RenderingContext is not set');
	}
	return context;
};

export const useCanUseUndoStack = () => {
	const context = useContext(CanUseUndoStackContext);
	if (!context) {
		throw new Error('CanUseUndoStackContext is not set');
	}
	return context;
};

export const useAssetFromAssetId = (assetId: string): EditorStarterAsset => {
	const {assets} = useAssets();
	const asset = findAssetById(assets, assetId);
	if (!asset) {
		throw new Error('Asset not found');
	}
	return asset;
};

export const useOptionalAssetFromAssetId = (
	assetId: string,
): EditorStarterAsset | null => {
	const {assets} = useAssets();
	const asset = findAssetById(assets, assetId);
	return asset ?? null;
};

export const useAssetIfApplicable = (
	item: EditorStarterItem,
): EditorStarterAsset | null => {
	const {assets} = useAssets();

	const asset = getAssetFromItem({item, assets});

	return asset;
};

export const useAssetFromItem = (
	item: EditorStarterItem,
): EditorStarterAsset => {
	const asset = useAssetIfApplicable(item);
	if (!asset) {
		throw new Error('Asset not found');
	}
	return asset;
};

export const useActiveTimelineSnap = () => {
	const context = useContext(ActiveTimelineSnapContext);
	if (!context) {
		throw new Error('ActiveTimelineSnapContext is not set');
	}
	return context;
};

export const useActiveCanvasSnapPoints = () => {
	const context = useContext(ActiveCanvasSnapContext);
	if (!context) {
		throw new Error('ActiveCanvasSnapContext is not set');
	}
	return context;
};
