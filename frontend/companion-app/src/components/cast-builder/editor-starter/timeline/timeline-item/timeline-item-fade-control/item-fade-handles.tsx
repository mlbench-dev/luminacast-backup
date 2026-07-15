import React, {useMemo} from 'react';
import {AbsoluteFill} from 'remotion';
import {EditorStarterItem} from '../../../items/item-type';
import {useAssetIfApplicable, useFps} from '../../../utils/use-context';
import {getWaveformHeight} from '../timeline-item-waveform/timeline-item-waveform';
import {calculateTotalItemDuration} from './fade-curve';
import {FADE_HANDLE_WIDTH} from './fade-drag-handles';
import {getFadeValue} from './get-fade-value';
import {ItemFadeHandle} from './item-fade-handle';
import {useFadeDrag} from './use-fade-drag';

export type FadeType = 'in' | 'out';
export type FadeMediaType = 'audio' | 'visual';

interface ItemFadeHandlesProps {
	item: EditorStarterItem;
	width: number;
	itemHeight: number;
	hovered: boolean;
	fadeType: FadeMediaType;
}

export function ItemFadeHandles({
	item,
	width,
	itemHeight,
	hovered,
	fadeType,
}: ItemFadeHandlesProps) {
	const {fps} = useFps();
	const asset = useAssetIfApplicable(item);

	const fadeInDrag = useFadeDrag({
		item,
		type: 'in',
		width,
		fadeType,
	});
	const fadeOutDrag = useFadeDrag({
		item,
		type: 'out',
		width,
		fadeType,
	});

	const totalDuration = useMemo(
		() => calculateTotalItemDuration(item, fps),
		[item, fps],
	);

	const isDragging = fadeInDrag.isDragging || fadeOutDrag.isDragging;
	const showHandle = isDragging || hovered;

	const top = useMemo(() => {
		if (fadeType === 'visual') {
			return 0;
		}

		return Math.max(
			0,
			itemHeight - 2 - getWaveformHeight({item, trackHeight: itemHeight}),
		);
	}, [itemHeight, item, fadeType]);

	const style: React.CSSProperties = useMemo(() => {
		return {
			top,
			height: 0,
		};
	}, [top]);

	if (width < FADE_HANDLE_WIDTH * 4) {
		return null;
	}

	if (
		asset &&
		asset.type === 'video' &&
		!asset.hasAudioTrack &&
		fadeType === 'audio'
	) {
		return null;
	}

	return (
		<AbsoluteFill style={style}>
			<ItemFadeHandle
				fadeDrag={fadeInDrag}
				fadeDuration={getFadeValue({
					item,
					fadeProperty:
						fadeType === 'audio'
							? 'audioFadeInDurationInSeconds'
							: 'fadeInDurationInSeconds',
				})}
				totalDuration={totalDuration}
				width={width}
				showHandle={showHandle}
				type="in"
			/>
			<ItemFadeHandle
				fadeDrag={fadeOutDrag}
				fadeDuration={getFadeValue({
					item,
					fadeProperty:
						fadeType === 'audio'
							? 'audioFadeOutDurationInSeconds'
							: 'fadeOutDurationInSeconds',
				})}
				totalDuration={totalDuration}
				width={width}
				showHandle={showHandle}
				type="out"
			/>
		</AbsoluteFill>
	);
}
