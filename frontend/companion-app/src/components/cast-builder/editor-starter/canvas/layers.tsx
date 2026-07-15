import React, {useRef} from 'react';
import {AbsoluteFill} from 'remotion';
import {Layer} from '../items/layer';
import {TrackType} from '../state/types';
import {useForbidScroll} from '../utils/forbid-scroll';

const layerWrapperStyle: React.CSSProperties = {
	overflow: 'hidden',
};

const LayersUnmemoized: React.FC<{
	tracks: TrackType[];
}> = ({tracks}) => {
	const layerWrapperRef = useRef<HTMLDivElement | null>(null);
	useForbidScroll(layerWrapperRef);

	return (
		<AbsoluteFill style={layerWrapperStyle} ref={layerWrapperRef}>
			{tracks
				.slice()
				.reverse()
				.map((track) => {
					if (track.hidden) {
						return null;
					}

					return (
						<React.Fragment key={track.id}>
							{track.items.map((itemId) => {
								return (
									<Layer
										key={itemId}
										itemId={itemId}
										trackMuted={track.muted}
									/>
								);
							})}
						</React.Fragment>
					);
				})}
		</AbsoluteFill>
	);
};

export const Layers = React.memo(LayersUnmemoized);
