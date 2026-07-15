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
	const itemIsBeingCropped = item.id === itemSelectedForCrop;

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
