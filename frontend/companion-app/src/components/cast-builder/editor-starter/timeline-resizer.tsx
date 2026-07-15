import {useCallback, useMemo} from 'react';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from './force-specific-cursor';
import {
	getMaxTimelineHeight,
	getMinTimelineHeight,
	saveTimelineHeight,
} from './state/timeline-height-persistance';
import {clamp} from './utils/clamp';
import {isLeftClick} from './utils/is-left-click';
import {useCurrentStateAsRef, useWriteContext} from './utils/use-context';
import {Z_INDEX_TIMELINE_RESIZER} from './z-indices';

const RESIZER_HEIGHT = 5;

export const TimelineResizer = () => {
	const state = useCurrentStateAsRef();
	const {setState} = useWriteContext();

	const style = useMemo((): React.CSSProperties => {
		return {
			transformOrigin: 'bottom',
			cursor: 'row-resize',
			height: RESIZER_HEIGHT,
			zIndex: Z_INDEX_TIMELINE_RESIZER,
		};
	}, []);

	const onPointerDown = useCallback(
		(e: React.PointerEvent) => {
			if (!isLeftClick(e)) {
				return;
			}

			const initialY = e.clientY;
			const initialHeight = state.current.timelineHeight;

			forceSpecificCursor('row-resize');

			const onPointerMove = (moveEvent: PointerEvent) => {
				const deltaY = initialY - moveEvent.clientY;

				const newTimelineHeight = clamp({
					value: initialHeight + deltaY,
					min: getMinTimelineHeight(),
					max: getMaxTimelineHeight(),
				});

				setState({
					update: (prev) => {
						if (newTimelineHeight === prev.timelineHeight) {
							return prev;
						}

						return {
							...prev,
							timelineHeight: newTimelineHeight,
						};
					},
					commitToUndoStack: false,
				});
			};

			const onPointerUp = () => {
				saveTimelineHeight(state.current.timelineHeight);
				window.removeEventListener('pointermove', onPointerMove);
				stopForcingSpecificCursor();
			};

			window.addEventListener('pointermove', onPointerMove);
			window.addEventListener('pointerup', onPointerUp, {once: true});
		},
		[state, setState],
	);

	return (
		<div className="relative w-full">
			<div
				className="absolute w-full flex-shrink-0 select-none"
				style={style}
				onPointerDown={onPointerDown}
			/>
		</div>
	);
};
