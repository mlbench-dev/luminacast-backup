import {EditorStarterAsset} from '../assets/assets';
import {
	periodicallyCheckIfLocalUrlIsStillValid,
	useLocalUrls,
} from '../caching/load-to-blob-url';

export const findAssetById = (
	assets: Record<string, EditorStarterAsset>,
	id: string,
): EditorStarterAsset | undefined => {
	return assets[id];
};

export const usePreferredLocalUrl = (asset: EditorStarterAsset) => {
	const localUrls = useLocalUrls();

	// Always prefer local URL if available
	if (localUrls[asset.id]) {
		periodicallyCheckIfLocalUrlIsStillValid(localUrls[asset.id], asset);
		return localUrls[asset.id];
	}

	// Only use remote URL if upload is complete
	if (asset.remoteUrl) {
		return asset.remoteUrl;
	}

	// If no local URL and upload not complete, throw error
	throw new Error(`Asset ${asset.id} has neither remote nor local URL`);
};

export const useRequireLocalUrl = (asset: EditorStarterAsset) => {
	const localUrls = useLocalUrls();

	// Always prefer local URL if available
	if (localUrls[asset.id]) {
		periodicallyCheckIfLocalUrlIsStillValid(localUrls[asset.id], asset);
		return localUrls[asset.id];
	}

	return null;
};
