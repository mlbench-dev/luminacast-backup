import {PlayerRef} from '@remotion/player';
import React, {useEffect} from 'react';
import {isEventTargetInputElement} from './is-event-target-input-element';

export const useKeyboardControls = ({
	playerRef,
	fps,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
	fps: number;
}) => {
	useEffect(() => {
		const {current} = playerRef;
		if (!current) {
			return;
		}

		const onKeydown = (e: KeyboardEvent) => {
			if (isEventTargetInputElement(e)) {
				return;
			}

			const moveBy = e.shiftKey ? fps : 1;
			if (e.key === 'ArrowRight') {
				e.preventDefault();
				current.seekTo(current.getCurrentFrame() + moveBy);
			}

			if (e.key === 'ArrowLeft') {
				e.preventDefault();
				current.seekTo(current.getCurrentFrame() - moveBy);
			}
		};

		window.addEventListener('keydown', onKeydown);

		return () => {
			window.removeEventListener('keydown', onKeydown);
		};
	}, [fps, playerRef]);
};
