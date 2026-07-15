import {useCallback} from 'react';
import {toast} from 'sonner';
import {DownloadIcon} from '../icons/download-state';
import {
	cleanUpAssetStatus,
	cleanUpStateBeforeSaving,
} from '../state/clean-up-state-before-saving';
import {clsx} from '../utils/clsx';
import {hasUploadingAssets} from '../utils/upload-status';
import {useFullState} from '../utils/use-context';

export const DownloadStateButton = () => {
	const state = useFullState();

	const handleDownload = useCallback(() => {
		try {
			const cleanedUpState = cleanUpAssetStatus(state);
			const stateToDownload = cleanUpStateBeforeSaving(
				cleanedUpState.undoableState,
			);

			// Create a blob with the state data
			const dataStr = JSON.stringify(stateToDownload, null, 2);
			const dataBlob = new Blob([dataStr], {type: 'application/json'});

			// Create download link
			const url = URL.createObjectURL(dataBlob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `editor-state-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`;

			// Trigger download
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);

			// Clean up URL
			URL.revokeObjectURL(url);

			toast.success('State downloaded successfully');
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : 'Failed to download state',
			);
		}
	}, [state]);

	const assetsUploading = hasUploadingAssets(state.assetStatus);

	return (
		<div className="bg-white/5">
			<button
				data-uploading={Boolean(assetsUploading)}
				className={clsx(
					'editor-starter-focus-ring flex h-10 w-10 items-center justify-center rounded text-white transition-colors',
					assetsUploading && 'opacity-50',
					!assetsUploading && 'hover:bg-white/10',
				)}
				title={
					assetsUploading
						? 'Cannot download while assets are getting uploaded to the cloud'
						: 'Download state'
				}
				disabled={assetsUploading}
				onClick={handleDownload}
				aria-label="Download state"
			>
				<DownloadIcon />
			</button>
		</div>
	);
};
