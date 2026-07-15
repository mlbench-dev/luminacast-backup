import {PlayerRef} from '@remotion/player';
import React, {useCallback, useContext} from 'react';
import {addAsset} from '../assets/add-asset';
import {EditModeContext} from '../edit-mode';
import {
	FEATURE_CREATE_TEXT_TOOL,
	FEATURE_DRAW_SOLID_TOOL,
	FEATURE_IMPORT_ASSETS_TOOL,
} from '../flags';
import {AudioIcon} from '../icons/audio-icon';
import {EditModeIcon} from '../icons/edit-mode';
import {ImageIcon} from '../icons/image';
import {SolidIcon} from '../icons/solid';
import {TextIcon} from '../icons/text';
import {VideoIcon} from '../icons/video';
import {useCurrentStateAsRef, useWriteContext} from '../utils/use-context';

export const ToolSelection: React.FC<{
	playerRef: React.RefObject<PlayerRef | null>;
}> = ({playerRef}) => {
	const timelineWriteContext = useWriteContext();
	const {editMode, setEditMode} = useContext(EditModeContext);

	const setSelectEditMode = useCallback(() => {
		setEditMode('select');
	}, [setEditMode]);

	const setSolidEditMode = useCallback(() => {
		setEditMode('draw-solid');
	}, [setEditMode]);

	const setCreateTextMode = useCallback(() => {
		setEditMode('create-text');
	}, [setEditMode]);

	const fileInputRef = React.useRef<HTMLInputElement>(null);

	const addFile = useCallback(() => {
		fileInputRef.current?.click();
	}, []);

	const stateAsRef = useCurrentStateAsRef();

	const handleFileChange = useCallback(
		async (e: React.ChangeEvent<HTMLInputElement>) => {
			const files = e.target.files;
			if (!files) return;

			const uploadPromises = [];
			for (const file of files) {
				uploadPromises.push(
					addAsset({
						file,
						timelineWriteContext: timelineWriteContext,
						playerRef,
						dropPosition: null,
						fps: stateAsRef.current.undoableState.fps,
						compositionWidth: stateAsRef.current.undoableState.compositionWidth,
						compositionHeight:
							stateAsRef.current.undoableState.compositionHeight,
						tracks: stateAsRef.current.undoableState.tracks,
						filename: file.name,
					}),
				);
			}
			await Promise.all(uploadPromises);
			// Allow for more files to be added
			e.target.value = '';
		},
		[playerRef, stateAsRef, timelineWriteContext],
	);
	return (
		<>
			<input
				ref={fileInputRef}
				type="file"
				accept="image/*,video/*,audio/*"
				onChange={handleFileChange}
				className="hidden"
				multiple
			/>
			<div className="flex overflow-hidden rounded bg-white/5">
				<button
					data-active={editMode === 'select'}
					className="editor-starter-focus-ring flex h-10 w-10 items-center justify-center text-white transition-colors hover:bg-white/10 data-[active=true]:bg-white/10"
					title="Select"
					onClick={setSelectEditMode}
					aria-label="Select"
				>
					<EditModeIcon
						fill="none"
						stroke="currentColor"
						strokeWidth="2"
						className="w-4"
					/>
				</button>

				{FEATURE_DRAW_SOLID_TOOL ? (
					<>
						<div className="bg-editor-starter-panel w-px"></div>
						<button
							onClick={setSolidEditMode}
							data-active={editMode === 'draw-solid'}
							className="editor-starter-focus-ring flex h-10 w-10 items-center justify-center text-white transition-colors hover:bg-white/10 data-[active=true]:bg-white/10"
							title="Add Solid"
							aria-label="Add Solid"
						>
							<SolidIcon className="w-4" />
						</button>
					</>
				) : null}
				{FEATURE_CREATE_TEXT_TOOL ? (
					<>
						<div className="bg-editor-starter-panel w-px"></div>
						<button
							onClick={setCreateTextMode}
							data-active={editMode === 'create-text'}
							className="editor-starter-focus-ring flex h-10 w-10 items-center justify-center text-white transition-colors hover:bg-white/10 data-[active=true]:bg-white/10"
							title="Add Text"
							aria-label="Add Text"
						>
							<TextIcon className="w-4" />
						</button>
					</>
				) : null}
				{FEATURE_IMPORT_ASSETS_TOOL ? (
					<>
						<div className="bg-editor-starter-panel w-px"></div>
						<button
							onClick={addFile}
							className="editor-starter-focus-ring flex h-10 items-center justify-center gap-3 px-3 text-white transition-colors hover:bg-white/10"
							title="Add images, videos, and audio"
							aria-label="Add images, videos, and audio"
						>
							<ImageIcon />
							<VideoIcon />
							<AudioIcon />
						</button>
					</>
				) : null}
			</div>
		</>
	);
};
