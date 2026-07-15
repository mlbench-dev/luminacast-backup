import {AssetState, EditorStarterAsset} from '../assets/assets';
import {getKeys} from '../caching/indexeddb';
import {EditorState, UndoableState} from '../state/types';
import {checkFileExists} from '../utils/assets';

export type AssetWithStatus = EditorStarterAsset & {
	status: AssetState;
};

/**
 * Get an asset with its status merged in
 */
export const getAssetWithStatus = (
	state: EditorState,
	assetId: string,
): AssetWithStatus | null => {
	const asset = state.undoableState.assets[assetId];
	const status = state.assetStatus[assetId];

	if (!asset || !status) {
		return null;
	}

	return {
		...asset,
		status,
	};
};

/**
 * Get all assets with their status merged in
 */
export const getAllAssetsWithStatus = (
	state: EditorState,
): Record<string, AssetWithStatus> => {
	const result: Record<string, AssetWithStatus> = {};

	for (const [assetId, asset] of Object.entries(state.undoableState.assets)) {
		const status = state.assetStatus[assetId];
		if (status) {
			result[assetId] = {
				...asset,
				status,
			};
		}
	}

	return result;
};

/**
 * Get the status of an asset
 */
export const getAssetStatus = (
	state: EditorState,
	assetId: string,
): AssetState | null => {
	return state.assetStatus[assetId] || null;
};

export const createAssetStatusFromUndoableState = async (
	state: UndoableState,
): Promise<Record<string, AssetState>> => {
	const result: Record<string, AssetState> = {};

	// Get all assets that exist in IndexedDB
	let indexedDBKeys: IDBValidKey[] = [];
	try {
		indexedDBKeys = await getKeys();
	} catch {
		// If IndexedDB is not available, continue without checking local storage
		// Assets will be marked as error if they don't have remoteUrl
	}

	for (const [assetId, asset] of Object.entries(state.assets)) {
		if (asset.remoteUrl) {
			const checkIfFileExists = await checkFileExists(asset.remoteUrl);
			if (checkIfFileExists) {
				result[assetId] = {
					type: 'uploaded',
				};
			}
		} else if (asset.type === 'caption') {
			result[assetId] = {
				type: 'uploaded',
			};
		} else {
			// Asset has no remote URL - check if it exists in IndexedDB
			const existsInIndexedDB = indexedDBKeys.includes(assetId);

			if (existsInIndexedDB) {
				result[assetId] = {
					type: 'error',
					error: new Error('No credentials to upload'),
					canRetry: true,
				};
			} else {
				// Asset doesn't exist locally or remotely
				result[assetId] = {
					type: 'error',
					error: new Error('Asset not found'),
					canRetry: false,
				};
			}
		}
	}

	return result;
};

export const hasAssetsWithErrors = (
	assetStatus: Record<string, AssetState>,
): boolean => {
	return Object.values(assetStatus).some((status) => status.type === 'error');
};
