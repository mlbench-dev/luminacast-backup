import React, {useEffect} from 'react';
import {toast} from 'sonner';
import {saveButtonRef} from '../action-row/save-button';
import {FEATURE_SAVE_SHORTCUT} from '../flags';
import {
	cleanUpAssetStatus,
	cleanUpStateBeforeSaving,
} from '../state/clean-up-state-before-saving';
import {saveState} from '../state/persistance';
import {useCurrentStateAsRef} from '../utils/use-context';

export const SaveShortcut: React.FC = () => {
	const stateAsRef = useCurrentStateAsRef();

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			// Save: Cmd+S (Mac) or Ctrl+S (Windows/Linux)
			if ((e.metaKey || e.ctrlKey) && e.key === 's' && FEATURE_SAVE_SHORTCUT) {
				e.preventDefault();

				try {
					const cleanedUpState = cleanUpAssetStatus(stateAsRef.current);
					saveState(
						cleanUpStateBeforeSaving(cleanedUpState.undoableState),
						cleanedUpState.assetStatus,
					);
					saveButtonRef.current?.setLastSavedState(
						cleanedUpState.undoableState,
					);
				} catch (error) {
					toast.error(
						error instanceof Error
							? error.message
							: 'An unknown error occurred',
					);
				}
			}
		};

		window.addEventListener('keydown', handleKeyDown);

		return () => {
			window.removeEventListener('keydown', handleKeyDown);
		};
	}, [stateAsRef]);

	return null;
};
