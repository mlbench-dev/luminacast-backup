import React, {
	useCallback,
	useImperativeHandle,
	useMemo,
	useState,
} from 'react';
import {toast} from 'sonner';
import {SaveIcon} from '../icons/save';
import {
	cleanUpAssetStatus,
	cleanUpStateBeforeSaving,
} from '../state/clean-up-state-before-saving';
import {saveState} from '../state/persistance';
import {UndoableState} from '../state/types';
import {hasAssetsWithErrors} from '../utils/asset-status-utils';
import {clsx} from '../utils/clsx';
import {hasUploadingAssets} from '../utils/upload-status';
import {useFullState} from '../utils/use-context';

export const saveButtonRef = React.createRef<{
	setLastSavedState: (state: UndoableState) => void;
}>();

export const SaveButton = () => {
	const state = useFullState();
	const [lastSavedState, setLastSavedState] = useState<UndoableState | null>(
		null,
	);

	const handleSave = useCallback(() => {
		try {
			const cleanedUpState = cleanUpAssetStatus(state);
			saveState(
				cleanUpStateBeforeSaving(cleanedUpState.undoableState),
				cleanedUpState.assetStatus,
			);
			setLastSavedState(cleanedUpState.undoableState);
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : 'An unknown error occurred',
			);
			return;
		}
	}, [state]);

	useImperativeHandle(saveButtonRef, () => ({
		setLastSavedState,
	}));

	const isSavedState = lastSavedState === state.undoableState;
	const assetsUploading = hasUploadingAssets(state.assetStatus);
	const assetsWithErrors = hasAssetsWithErrors(state.assetStatus);

	const title = useMemo(() => {
		if (assetsWithErrors) {
			return 'Cannot save: Some assets have errors';
		}
		if (assetsUploading) {
			return 'Cannot save while assets are getting uploaded to the cloud';
		}
	}, [assetsWithErrors, assetsUploading]);

	const isDisabled = useMemo(() => {
		return isSavedState || assetsUploading || assetsWithErrors;
	}, [isSavedState, assetsUploading, assetsWithErrors]);

	return (
		<div className="bg-white/5">
			<button
				data-saved={Boolean(isSavedState)}
				data-uploading={Boolean(assetsUploading)}
				data-has-errors={Boolean(assetsWithErrors)}
				className={clsx(
					'editor-starter-focus-ring flex h-10 w-10 items-center justify-center rounded text-white transition-colors',
					(isSavedState || assetsUploading || assetsWithErrors) && 'opacity-50',
					!isSavedState &&
						!assetsUploading &&
						!assetsWithErrors &&
						'hover:bg-white/10',
				)}
				title={title}
				disabled={isDisabled}
				onClick={handleSave}
				aria-label={title}
			>
				<SaveIcon />
			</button>
		</div>
	);
};
