import {memo} from 'react';
import {CAPTION_PRESETS, DEFAULT_CAPTION_PRESET_ID} from '@/lib/captionPresets';
import {EditorStarterAsset} from '../../assets/assets';
import {RequireCachedAsset} from '../../caching/require-cached-asset';
import {
	FEATURE_AUDIO_WAVEFORM_FOR_VIDEO_ITEM,
	FEATURE_FILMSTRIP,
	FEATURE_TIMELINE_VOLUME_CONTROL,
	FEATURE_VISUAL_FADE_CONTROL,
	FEATURE_WAVEFORM,
} from '../../flags';
import {AudioItem} from '../../items/audio/audio-item-type';
import {EditorStarterItem} from '../../items/item-type';
import {VideoItem} from '../../items/video/video-item-type';
import {TRACK_PADDING} from '../../state/items';
import {getCanFadeVisual} from '../../utils/fade';
import {shouldShowItemHeader} from '../../utils/position-utils';
import {useAssetIfApplicable, useFps} from '../../utils/use-context';
import {useTimelineSize} from '../utils/use-timeline-size';
import {FadeCurve} from './timeline-item-fade-control/fade-curve';
import {
	FILMSTRIP_HEIGHT_IF_THERE_IS_AUDIO,
	TimelineItemFilmStrip,
} from './timeline-item-film-strip/timeline-item-film-strip';
import {TimelineItemHeader} from './timeline-item-header';
import {TIMELINE_ITEM_BORDER_WIDTH} from './timeline-item-layout';
import {TimelineItemPreview} from './timeline-item-preview';
import {TimelineItemVolumeControl} from './timeline-item-volume-control/timeline-item-volume-control';
import {TimelineItemWaveform} from './timeline-item-waveform/timeline-item-waveform';

const shouldShowAudioWaveform = ({
	item,
	asset,
}: {
	item: EditorStarterItem;
	asset: EditorStarterAsset | null;
}) => {
	if (!FEATURE_WAVEFORM) {
		return false;
	}

	if (item.type === 'audio') {
		return true;
	}

	if (item.type === 'video') {
		if (!asset || asset.type !== 'video') {
			throw new Error('Expected video asset in shouldShowAudioWaveform');
		}

		return FEATURE_AUDIO_WAVEFORM_FOR_VIDEO_ITEM && asset.hasAudioTrack;
	}

	// Add all item types that don't have audio waveform here
	if (
		item.type === 'captions' ||
		item.type === 'gif' ||
		item.type === 'text' ||
		item.type === 'solid' ||
		item.type === 'image'
	) {
		return false;
	}

	throw new Error('Invalid item type: ' + (item satisfies never));
};

export const TimelineItemContent = memo(
	({
		item,
		height,
		width,
		roundedDifference,
		trackMuted,
	}: {
		item: EditorStarterItem;
		height: number;
		width: number;
		roundedDifference: number;
		trackMuted: boolean;
	}) => {
		const {fps} = useFps();
		const asset = useAssetIfApplicable(item);
		const {timelineWidth} = useTimelineSize();

		if (timelineWidth === null) {
			throw new Error('Timeline width is null');
		}

		const filmstripHeight =
			asset?.type === 'video' && !asset.hasAudioTrack
				? height
				: FILMSTRIP_HEIGHT_IF_THERE_IS_AUDIO;

		// Caption preset indicator — a tiny colored bar on the left edge of
		// the caption track item so the user can tell at a glance which
		// preset applies to which block. Color comes from the preset's own
		// highlightColor (falls back to color or a neutral) so the bar
		// matches the chip in the Caption Style bar.
		const captionPresetId =
			item.type === 'captions'
				? ((item.metadata?.caption_preset as string | undefined) ??
						DEFAULT_CAPTION_PRESET_ID)
				: null;
		const presetColor = (() => {
			if (!captionPresetId) return null;
			const preset = CAPTION_PRESETS[captionPresetId];
			if (!preset) return 'rgba(255,255,255,0.25)';
			const c = preset.highlightColor;
			if (!c || c === '#FFFFFF' || c === '#ffffff' || c === 'transparent') {
				return preset.color && preset.color !== 'transparent' && preset.color !== '#FFFFFF'
					? preset.color
					: 'rgba(255,255,255,0.45)';
			}
			return c;
		})();

		return (
			<>
				<div className="absolute h-full w-full">
					<TimelineItemPreview item={item} />
					{presetColor ? (
						<div
							className="pointer-events-none absolute left-0 top-0 z-10 h-full w-1 rounded-l"
							style={{backgroundColor: presetColor}}
							title={`Caption preset: ${captionPresetId}`}
						/>
					) : null}
					{item.type === 'video' && FEATURE_FILMSTRIP && asset && (
						<RequireCachedAsset asset={asset}>
							<TimelineItemFilmStrip
								item={item}
								startFrom={item.videoStartFromInSeconds * fps}
								durationInFrames={item.durationInFrames}
								fps={fps}
								roundedDifference={roundedDifference}
								height={filmstripHeight}
								playbackRate={item.playbackRate}
							/>
						</RequireCachedAsset>
					)}
					{getCanFadeVisual(item) && FEATURE_VISUAL_FADE_CONTROL && (
						<FadeCurve
							item={item}
							height={
								item.type === 'video'
									? filmstripHeight
									: height - TRACK_PADDING - TIMELINE_ITEM_BORDER_WIDTH * 2
							}
							width={width}
							fadeType="visual"
						/>
					)}
					{asset && shouldShowAudioWaveform({item, asset}) ? (
						<RequireCachedAsset asset={asset}>
							<TimelineItemWaveform
								trackHeight={height}
								item={item as AudioItem | VideoItem}
								timelineWidth={timelineWidth}
								roundedDifference={roundedDifference}
								trackMuted={trackMuted}
							>
								{FEATURE_TIMELINE_VOLUME_CONTROL ? (
									<TimelineItemVolumeControl
										item={item as AudioItem | VideoItem}
									/>
								) : null}
							</TimelineItemWaveform>
						</RequireCachedAsset>
					) : null}
					{shouldShowItemHeader(item) && asset && asset.filename ? (
						<TimelineItemHeader filename={asset.filename} item={item} />
					) : null}
				</div>
			</>
		);
	},
);

TimelineItemContent.displayName = 'TimelineItemContent';
