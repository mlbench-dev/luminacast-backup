import React, {useEffect} from 'react';
import {FEATURE_REDO_SHORTCUT, FEATURE_UNDO_SHORTCUT} from '../flags';
import {isEventTargetInputElement} from '../utils/is-event-target-input-element';
import {useWriteContext} from '../utils/use-context';

export const UndoRedo: React.FC = () => {
	const {undo, redo} = useWriteContext();

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			// do not trigger if the target is an input
			if (isEventTargetInputElement(e)) {
				return;
			}

			// Undo: Cmd+Z (Mac) or Ctrl+Z (Windows/Linux)
			if (
				(e.metaKey || e.ctrlKey) &&
				e.key === 'z' &&
				!e.shiftKey &&
				FEATURE_UNDO_SHORTCUT
			) {
				e.preventDefault();
				undo();
			}

			// Redo: Cmd+Shift+Z (Mac) or Ctrl+Y (Windows/Linux) or Ctrl+Shift+Z
			if (
				((e.metaKey || e.ctrlKey) &&
					e.key === 'z' &&
					e.shiftKey &&
					FEATURE_REDO_SHORTCUT) ||
				((e.metaKey || e.ctrlKey) && e.key === 'y' && FEATURE_REDO_SHORTCUT)
			) {
				e.preventDefault();
				redo();
			}
		};

		window.addEventListener('keydown', handleKeyDown);

		return () => {
			window.removeEventListener('keydown', handleKeyDown);
		};
	}, [undo, redo]);

	return null;
};
