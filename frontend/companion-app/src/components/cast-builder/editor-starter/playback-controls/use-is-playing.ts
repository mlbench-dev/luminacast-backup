import {PlayerRef} from '@remotion/player';
import React, {useEffect} from 'react';

export const useIsPlaying = (playerRef: React.RefObject<PlayerRef | null>) => {
	const [playing, setPlaying] = React.useState(
		() => playerRef.current?.isPlaying() ?? false,
	);

	useEffect(() => {
		const {current} = playerRef;
		if (!current) {
			return;
		}

		const onPlay = () => {
			setPlaying(true);
		};

		const onPause = () => {
			setPlaying(false);
		};

		current.addEventListener('play', onPlay);
		current.addEventListener('pause', onPause);

		return () => {
			current.removeEventListener('play', onPlay);
			current.removeEventListener('pause', onPause);
		};
	}, [playerRef]);

	return playing;
};
