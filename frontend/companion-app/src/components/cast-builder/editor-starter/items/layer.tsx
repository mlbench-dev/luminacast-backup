import React, {useContext, useMemo} from 'react';
import {Sequence, useVideoConfig} from 'remotion';
import {ItemSelectedForCropContext} from '../context-provider';
import {FEATURE_CROP_BACKGROUNDS} from '../flags';
import {useItem} from '../utils/use-context';
import {InnerLayer} from './inner-layer';

export const Layer: React.FC<{
	itemId: string;
	trackMuted: boolean;
}> = ({itemId, trackMuted}) => {
	const {fps} = useVideoConfig();
	const item = useItem(itemId);
	const itemSelectedForCrop = useContext(ItemSelectedForCropContext);

	const sequenceStyle: React.CSSProperties = useMemo(
		() => ({
			display: 'contents',
		}),
		[],
	);

	const styleWhilePremounted: React.CSSProperties = useMemo(
		() => ({
			display: 'block',
		}),
		[],
	);

	// A track can reference an itemId that no longer exists in the items map
	// (e.g. a stale/orphaned reference left behind by a block that was
	// deleted or regenerated). Without this guard, the render below throws
	// inside Remotion's composition tree — which Remotion swallows into a
	// blank canvas instead of surfacing an error, with the rest of the page
	// (built from the same state via other code paths) looking unaffected.
	// (Placed after all hooks above so hook call order stays unconditional.)
	if (!item) {
		console.warn(`Layer: itemId "${itemId}" has no matching entry in items — skipping render for this item.`);
		return null;
	}

	const itemIsBeingCropped = item.id === itemSelectedForCrop;

	
	return (
		<>
			<Sequence
				key={item.id}
				from={item.from}
				style={sequenceStyle}
				durationInFrames={item.durationInFrames}
				styleWhilePremounted={styleWhilePremounted}
				premountFor={1.5 * fps}
			>
				{itemIsBeingCropped && FEATURE_CROP_BACKGROUNDS ? (
					// https://www.remotion.dev/docs/editor-starter/cropping#crop-backgrounds
					<InnerLayer cropBackground={true} item={item} trackMuted />
				) : null}
				<InnerLayer
					cropBackground={false}
					item={item}
					trackMuted={trackMuted}
				/>
			</Sequence>
		</>
	);
};
