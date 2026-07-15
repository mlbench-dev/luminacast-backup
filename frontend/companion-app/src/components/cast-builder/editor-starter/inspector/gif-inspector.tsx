import React from 'react';
import {
	FEATURE_ALIGNMENT_CONTROL,
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
} from '../flags';
import {GifItem} from '../items/gif/gif-item-type';
import {InspectorLabel} from './components/inspector-label';
import {
	CollapsableInspectorSection,
	InspectorDivider,
} from './components/inspector-section';
import {AlignmentControls} from './controls/alignment-controls';
import {BorderRadiusControl} from './controls/border-radius-controls';
import {CropControls} from './controls/crop-controls';
import {DimensionsControls} from './controls/dimensions-controls';
import {FadeControls} from './controls/fade-controls';
import {OpacityControls} from './controls/opacity-controls';
import {PlaybackRateControls} from './controls/playback-rate-controls';
import {PositionControl} from './controls/position-control';
import {RotationControl} from './controls/rotation-controls';
import {SourceControls} from './controls/source-info/source-info';

const GifInspectorUnmemoized: React.FC<{
	item: GifItem;
}> = ({item}) => {
	return (
		<div>
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
			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>GIF</InspectorLabel>}
				id={`gif-${item.id}`}
				defaultOpen={false}
			>
				{FEATURE_PLAYBACKRATE_CONTROL && (
					<PlaybackRateControls
						playbackRate={item.playbackRate}
						itemId={item.id}
						assetId={item.assetId}
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
			{FEATURE_VISUAL_FADE_CONTROL && (
				<>
					<InspectorDivider />
					<CollapsableInspectorSection
						summary={<InspectorLabel>Fade</InspectorLabel>}
						id={`fade-${item.id}`}
						defaultOpen={false}
					>
						<FadeControls
							fadeInDuration={item.fadeInDurationInSeconds}
							fadeOutDuration={item.fadeOutDurationInSeconds}
							itemId={item.id}
							durationInFrames={item.durationInFrames}
						/>
					</CollapsableInspectorSection>
				</>
			)}
		</div>
	);
};

export const GifInspector = React.memo(GifInspectorUnmemoized);
