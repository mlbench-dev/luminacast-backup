import React from 'react';
import {Clapperboard} from 'lucide-react';
import {GenerateCaptionSection} from '../captioning/caption-section';
import {
	FEATURE_ALIGNMENT_CONTROL,
	FEATURE_AUDIO_FADE_CONTROL,
	FEATURE_BORDER_RADIUS_CONTROL,
	FEATURE_CROP_CONTROL,
	FEATURE_CROPPING,
	FEATURE_DIMENSIONS_CONTROL,
	FEATURE_OPACITY_CONTROL,
	FEATURE_PLAYBACKRATE_CONTROL,
	FEATURE_POSITION_CONTROL,
	FEATURE_ROTATION_CONTROL,
	FEATURE_SOURCE_CONTROL,
	FEATURE_VISUAL_FADE_CONTROL,
	FEATURE_VOLUME_CONTROL,
} from '../flags';
import {VideoItem} from '../items/video/video-item-type';
import {useAssetFromItem} from '../utils/use-context';
import {InspectorLabel} from './components/inspector-label';
import {
	CollapsableInspectorSection,
	InspectorDivider,
} from './components/inspector-section';
import {AlignmentControls} from './controls/alignment-controls';
import {AudioFadeControls} from './controls/audio-fade-controls';
import {BorderRadiusControl} from './controls/border-radius-controls';
import {CropControls} from './controls/crop-controls';
import {DimensionsControls} from './controls/dimensions-controls';
import {FadeControls} from './controls/fade-controls';
import {OpacityControls} from './controls/opacity-controls';
import {PlaybackRateControls} from './controls/playback-rate-controls';
import {PositionControl} from './controls/position-control';
import {RotationControl} from './controls/rotation-controls';
import {SourceControls} from './controls/source-info/source-info';
import {VolumeControls} from './controls/volume-controls';
import {DurationModeControls} from './controls/duration-mode-controls';
import {SafeZoneWarning} from './controls/safe-zone-warning';

const VideoInspectorUnmemoized: React.FC<{
	item: VideoItem;
}> = ({item}) => {
	const asset = useAssetFromItem(item);
	const durationMode = item.metadata?.duration_mode ?? 'crop';

	if (asset.type !== 'video') {
		throw new Error('Video inspector not supported for video assets');
	}

	const isMotion = (item.metadata as any)?.render_mode === 'motion';
	const motionPrompt = (item.metadata as any)?.motion_prompt as
		| string
		| undefined;
	const pipLayout = (item.metadata as any)?.pip_layout as
		| 'fullscreen'
		| 'pip_small'
		| 'pip_medium'
		| 'hidden'
		| undefined;
	const isBondedV1 = Boolean((item.metadata as any)?.bonded && (item.metadata as any)?.track_type === 'video_face');
	const isPipElement = isBondedV1 && pipLayout && pipLayout !== 'fullscreen' && pipLayout !== 'hidden';

	return (
		<div>
			<SafeZoneWarning item={item} />
			{isMotion ? (
				<div className="m-3 rounded-lg border border-purple-500/20 bg-purple-500/10 p-3">
					<div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-purple-300">
						<Clapperboard className="h-3 w-3" /> Motion Description
					</div>
					<p className="text-xs leading-snug text-white/60">
						{motionPrompt || 'No motion description set'}
					</p>
				</div>
			) : null}
			{isPipElement ? (
				<div className="m-3 rounded-lg border border-white/10 bg-white/5 p-3 text-xs text-white/60">
					<div className="mb-1 text-[10px] uppercase tracking-wider text-white/40">
						Avatar window
					</div>
					<p className="leading-snug">
						This is the talking-head overlay for the block. Drag to
						reposition, use the corner handles or the Dimensions
						controls below to resize, and adjust opacity / border
						radius to taste. Changes persist to the timeline
						snapshot so the render matches what you see here.
					</p>
				</div>
			) : null}
			{FEATURE_SOURCE_CONTROL && <SourceControls item={item} />}
			<CollapsableInspectorSection
				summary={<InspectorLabel>Layout</InspectorLabel>}
				id={`layout-${item.id}`}
				defaultOpen
			>
				{FEATURE_ALIGNMENT_CONTROL && <AlignmentControls itemId={item.id} />}
				{FEATURE_POSITION_CONTROL && <PositionControl itemId={item.id} />}
				{FEATURE_DIMENSIONS_CONTROL && <DimensionsControls itemId={item.id} />}
				{FEATURE_ROTATION_CONTROL && (
					<RotationControl rotation={item.rotation} itemId={item.id} />
				)}
			</CollapsableInspectorSection>
			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>Fill</InspectorLabel>}
				id={`fill-${item.id}`}
				defaultOpen
			>
				{FEATURE_OPACITY_CONTROL && (
					<OpacityControls opacity={item.opacity} itemId={item.id} />
				)}
				{FEATURE_BORDER_RADIUS_CONTROL && (
					<BorderRadiusControl
						borderRadius={item.borderRadius}
						borderRadiusType="fill"
						itemId={item.id}
					/>
				)}
			</CollapsableInspectorSection>
			{FEATURE_CROPPING && FEATURE_CROP_CONTROL && (
				<>
					<InspectorDivider />
					<CollapsableInspectorSection
						summary={<InspectorLabel>Crop</InspectorLabel>}
						id={`crop-${item.id}`}
						defaultOpen={false}
					>
						<CropControls itemId={item.id} />
					</CollapsableInspectorSection>
				</>
			)}
			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>Video</InspectorLabel>}
				id={`video-${item.id}`}
				defaultOpen={false}
			>
				{FEATURE_PLAYBACKRATE_CONTROL && (
					<PlaybackRateControls
						playbackRate={item.playbackRate}
						itemId={item.id}
						assetId={item.assetId}
					/>
				)}
				{FEATURE_VISUAL_FADE_CONTROL && (
					<FadeControls
						fadeInDuration={item.fadeInDurationInSeconds}
						fadeOutDuration={item.fadeOutDurationInSeconds}
						itemId={item.id}
						durationInFrames={item.durationInFrames}
					/>
				)}
			</CollapsableInspectorSection>

			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>Duration</InspectorLabel>}
				id={`duration-mode-${item.id}`}
				defaultOpen={false}
			>
				<DurationModeControls
					itemId={item.id}
					currentMode={durationMode}
					isVideo
				/>
			</CollapsableInspectorSection>

			{asset.hasAudioTrack ? (
				<>
					<InspectorDivider />
					<CollapsableInspectorSection
						summary={<InspectorLabel>Audio</InspectorLabel>}
						id={`audio-${item.id}`}
						defaultOpen={false}
					>
						{FEATURE_VOLUME_CONTROL && (
							<VolumeControls
								decibelAdjustment={item.decibelAdjustment}
								itemId={item.id}
							/>
						)}
						{FEATURE_AUDIO_FADE_CONTROL && (
							<AudioFadeControls
								fadeInDuration={item.audioFadeInDurationInSeconds}
								fadeOutDuration={item.audioFadeOutDurationInSeconds}
								itemId={item.id}
								durationInFrames={item.durationInFrames}
							/>
						)}
					</CollapsableInspectorSection>
					<InspectorDivider />
					<GenerateCaptionSection item={item} />
				</>
			) : null}
		</div>
	);
};

export const VideoInspector = React.memo(VideoInspectorUnmemoized);
