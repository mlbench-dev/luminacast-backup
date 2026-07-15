import {CallbackListener, PlayerRef} from '@remotion/player';
import {useCallback, useEffect, useState} from 'react';

export const useTimelinePosition = ({
	playerRef,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
}) => {
	const [frame, setFrame] = useState(playerRef.current?.getCurrentFrame() ?? 0);

	const handleFrameUpdate: CallbackListener<'frameupdate'> = useCallback(
		(opts) => {
			setFrame(opts.detail.frame);
		},
		[],
	);

	useEffect(() => {
		const {current} = playerRef;
		if (!current) {
			return;
		}
		current.addEventListener('frameupdate', handleFrameUpdate);
		return () => {
			current.removeEventListener('frameupdate', handleFrameUpdate);
		};
	}, [handleFrameUpdate, playerRef]);

	return frame;
};
