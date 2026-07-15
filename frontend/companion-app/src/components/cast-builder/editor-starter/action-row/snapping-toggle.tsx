import {useCallback} from 'react';
import {MagnetIcon} from '../icons/magnet';
import {toggleSnapping} from '../state/actions/toggle-snapping';
import {saveSnappingEnabled} from '../state/snapping-persistance';
import {useSnappingEnabled, useWriteContext} from '../utils/use-context';

export function SnappingToggle() {
	const writeContext = useWriteContext();
	const isSnappingEnabled = useSnappingEnabled();

	const handleToggle = useCallback(() => {
		writeContext.setState({
			update: (state) => {
				const newState = toggleSnapping(state);
				saveSnappingEnabled(newState.isSnappingEnabled);
				return newState;
			},
			commitToUndoStack: false,
		});
	}, [writeContext]);

	return (
		<button
			onClick={handleToggle}
			className={`editor-starter-focus-ring flex items-center justify-center p-2 ${
				isSnappingEnabled ? 'text-blue-500' : 'text-neutral-300'
			}`}
			title={`${isSnappingEnabled ? 'Disable Snapping' : 'Enable Snapping'} (Shift+M)`}
			aria-label={isSnappingEnabled ? 'Disable Snapping' : 'Enable Snapping'}
			aria-pressed={isSnappingEnabled}
			aria-keyshortcuts="Shift+KeyM"
		>
			<MagnetIcon className="w-[14px]" />
		</button>
	);
}
