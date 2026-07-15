import {useEffect, useSyncExternalStore} from 'react';
import {FEATURE_CACHE_ASSETS_LOCALLY} from '../flags';
import {useAssets} from '../utils/use-context';
import {getKeysCache, onKeysChanged} from './indexeddb';
import {downloadToCacheOnce} from './load-to-blob-url';

const keysOnServer = () => null;

export const DownloadRemoteAssets: React.FC = () => {
	const keys = useSyncExternalStore(onKeysChanged, getKeysCache, keysOnServer);
	const {assets} = useAssets();

	useEffect(() => {
		if (!keys) {
			// IDB not yet loaded
			return;
		}

		if (!FEATURE_CACHE_ASSETS_LOCALLY) {
			return;
		}

		for (const assetId of Object.keys(assets)) {
			const isDownloaded = keys.includes(assetId);
			if (!isDownloaded) {
				downloadToCacheOnce(assets[assetId], null);
			}
		}
	}, [assets, keys]);

	return null;
};
