import {useCallback} from 'react';
import {RedoIcon} from '../icons/redo';
import {clsx} from '../utils/clsx';
import {useCanUseUndoStack, useWriteContext} from '../utils/use-context';

export const RedoButton = () => {
	const {redo} = useWriteContext();
	const {canRedo} = useCanUseUndoStack();

	const handleRedo = useCallback(() => {
		redo();
	}, [redo]);

	return (
		<div className="bg-white/5">
			<button
				className={clsx(
					'editor-starter-focus-ring flex h-10 w-10 items-center justify-center rounded text-white transition-colors',
					!canRedo && 'opacity-50',
					canRedo && 'hover:bg-white/10',
				)}
				disabled={!canRedo}
				title="Redo (Ctrl+Y / Cmd+Shift+Z)"
				onClick={handleRedo}
				aria-label="Redo"
			>
				<RedoIcon />
			</button>
		</div>
	);
};
