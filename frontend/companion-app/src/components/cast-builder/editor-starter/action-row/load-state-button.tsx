import {useCallback, useRef} from 'react';
import {toast} from 'sonner';
import {UploadIcon} from '../icons/upload';
import {EditorState, UndoableState} from '../state/types';
import {clsx} from '../utils/clsx';
import {hasUploadingAssets} from '../utils/upload-status';
import {useFullState, useWriteContext} from '../utils/use-context';

export const LoadStateButton = () => {
	const state = useFullState();
	const {setState} = useWriteContext();
	const fileInputRef = useRef<HTMLInputElement>(null);

	const handleLoadClick = useCallback(() => {
		fileInputRef.current?.click();
	}, []);

	const handleFileChange = useCallback(
		(event: React.ChangeEvent<HTMLInputElement>) => {
			const file = event.target.files?.[0];
			if (!file) {
				return;
			}

			// Validate file type
			if (!file.name.endsWith('.json')) {
				toast.error('Please select a valid JSON file');
				return;
			}

			const reader = new FileReader();
			reader.onload = (e) => {
				try {
					const result = e.target?.result;
					if (typeof result !== 'string') {
						throw new Error('Failed to read file');
					}

					const loadedState: UndoableState = JSON.parse(result);

					// Basic validation of loaded state structure
					if (
						!loadedState ||
						typeof loadedState !== 'object' ||
						!Array.isArray(loadedState.tracks) ||
						typeof loadedState.items !== 'object' ||
						typeof loadedState.assets !== 'object' ||
						typeof loadedState.fps !== 'number' ||
						typeof loadedState.compositionWidth !== 'number' ||
						typeof loadedState.compositionHeight !== 'number'
					) {
						throw new Error('Invalid state file format');
					}

					// Update the state
					setState({
						update: (prevState: EditorState) => ({
							...prevState,
							undoableState: loadedState,
						}),
						commitToUndoStack: true,
					});

					toast.success('State loaded successfully');
				} catch (error) {
					toast.error(
						error instanceof Error
							? `Failed to load state: ${error.message}`
							: 'Failed to load state',
					);
				}
			};

			reader.onerror = () => {
				toast.error('Failed to read file');
			};

			reader.readAsText(file);

			// Reset the input so the same file can be selected again
			event.target.value = '';
		},
		[setState],
	);

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
						? 'Cannot load while assets are getting uploaded to the cloud'
						: 'Load state from file'
				}
				disabled={assetsUploading}
				onClick={handleLoadClick}
				aria-label="Load state from file"
			>
				<UploadIcon />
			</button>
			<input
				ref={fileInputRef}
				type="file"
				accept=".json"
				style={{display: 'none'}}
				onChange={handleFileChange}
			/>
		</div>
	);
};
