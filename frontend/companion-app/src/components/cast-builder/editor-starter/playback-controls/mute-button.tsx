import {PlayerRef} from '@remotion/player';
import React, {useEffect, useState} from 'react';
import {MuteIcon} from '../icons/mute';
import {UnmuteIcon} from '../icons/unmute';

export const MuteButton: React.FC<{
	playerRef: React.RefObject<PlayerRef | null>;
}> = ({playerRef}) => {
	const [muted, setMuted] = useState(playerRef.current?.isMuted() ?? false);

	const onClick = React.useCallback(() => {
		if (!playerRef.current) {
			return;
		}

		if (playerRef.current.isMuted()) {
			playerRef.current.unmute();
		} else {
			playerRef.current.mute();
		}
	}, [playerRef]);

	useEffect(() => {
		const {current} = playerRef;
		if (!current) {
			return;
		}

		const onMuteChange = () => {
			setMuted(current.isMuted());
		};

		current.addEventListener('mutechange', onMuteChange);
		return () => {
			current.removeEventListener('mutechange', onMuteChange);
		};
	}, [playerRef]);

	return (
		<button
			className="editor-starter-focus-ring p-2"
			type="button"
			onClick={onClick}
			title="Mute"
			aria-label="Mute"
		>
			{muted ? (
				<UnmuteIcon className="text-editor-starter-accent size-5" />
			) : (
				<MuteIcon className="size-5 text-neutral-300" />
			)}
		</button>
	);
};
