import React, {useEffect} from 'react';
import {toggleSnapping} from '../state/actions/toggle-snapping';
import {saveSnappingEnabled} from '../state/snapping-persistance';
import {isEventTargetInputElement} from '../utils/is-event-target-input-element';
import {useWriteContext} from '../utils/use-context';

export const SnappingShortcut: React.FC = () => {
	const {setState} = useWriteContext();

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			// Avoid capturing typing and system shortcuts in inputs
			if (isEventTargetInputElement(e)) {
				return;
			}

			// Toggle snapping on Shift+M
			if (e.shiftKey && e.code === 'KeyM') {
				e.preventDefault();
				setState({
					update: (state) => {
						const newState = toggleSnapping(state);
						saveSnappingEnabled(newState.isSnappingEnabled);
						return newState;
					},
					commitToUndoStack: false,
				});
			}
		};

		window.addEventListener('keydown', handleKeyDown);
		return () => window.removeEventListener('keydown', handleKeyDown);
	}, [setState]);

	return null;
};
