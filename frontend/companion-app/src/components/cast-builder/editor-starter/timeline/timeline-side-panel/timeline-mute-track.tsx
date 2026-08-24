import {useCallback, useMemo} from 'react';
import {IconButton} from '../../icon-button';
import {MuteIcon} from '../../icons/mute';
import {UnmuteIcon} from '../../icons/unmute';
import {muteTrack, unmuteTrack} from '../../state/actions/mute-track';
import {TrackType} from '../../state/types';
import {useAllItems, useWriteContext} from '../../utils/use-context';

export const TimelineMuteTrack = ({track}: {track: TrackType}) => {
	const {setState} = useWriteContext();
	const {items} = useAllItems();

	// Mute only ever silences audio/video items (see InnerLayer — captions,
	// images, text, solids never receive trackMuted at all) — so a track
	// made up of anything else has nothing for this button to do. Disabling
	// it there instead of leaving it clickable-but-inert answers "why did
	// nothing happen when I muted this?" before the question comes up.
	const hasAudibleContent = useMemo(
		() => track.items.some((id) => {
			const item = items[id];
			return item?.type === 'audio' || item?.type === 'video';
		}),
		[track.items, items],
	);

	const toggle = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			e.preventDefault();

			if (track.muted) {
				setState({
					update: (state) => {
						return unmuteTrack(state, track.id);
					},
					commitToUndoStack: true,
				});
			} else {
				setState({
					update: (state) => {
						return muteTrack(state, track.id);
					},
					commitToUndoStack: true,
				});
			}
		},
		[track.muted, setState, track.id],
	);

	const onPointerDown = useCallback((e: React.PointerEvent) => {
		// Prevent items from being unselected
		e.stopPropagation();
	}, []);

	return (
		<IconButton
			onClick={hasAudibleContent ? toggle : undefined}
			onPointerDown={onPointerDown}
			disabled={!hasAudibleContent}
			aria-label={
				!hasAudibleContent
					? 'No audio on this track'
					: track.muted
						? 'Unmute Track'
						: 'Mute Track'
			}
			title={!hasAudibleContent ? 'This track has no audio to mute' : undefined}
			className={!hasAudibleContent ? 'cursor-not-allowed opacity-30' : undefined}
		>
			{track.muted ? (
				<UnmuteIcon className="text-editor-starter-accent size-4" />
			) : (
				<MuteIcon className="size-4 text-neutral-400" />
			)}
		</IconButton>
	);
};
