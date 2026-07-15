import React from 'react';
// Caption Fix 4 — captions are no longer managed from the audio inspector.
// Use the toolbar Captions toggle + the caption style bar + per-item
// click on the timeline instead.
import {
	FEATURE_AUDIO_FADE_CONTROL,
	FEATURE_PLAYBACKRATE_CONTROL,
	FEATURE_SOURCE_CONTROL,
	FEATURE_VOLUME_CONTROL,
} from '../flags';
import {AudioItem} from '../items/audio/audio-item-type';
import {InspectorLabel} from './components/inspector-label';
import {CollapsableInspectorSection} from './components/inspector-section';
import {AudioFadeControls} from './controls/audio-fade-controls';
import {PlaybackRateControls} from './controls/playback-rate-controls';
import {SourceControls} from './controls/source-info/source-info';
import {VolumeControls} from './controls/volume-controls';
import {BlockScriptEditor} from '../properties/BlockScriptEditor';

const AudioInspectorUnmemoized: React.FC<{
	item: AudioItem;
}> = ({item}) => {
	const isBonded = !!item.metadata?.block_id;

	return (
		<div>
			{FEATURE_SOURCE_CONTROL && <SourceControls item={item} />}
			{isBonded && <BlockScriptEditor item={item} />}
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
				{FEATURE_PLAYBACKRATE_CONTROL && (
					<PlaybackRateControls
						playbackRate={item.playbackRate}
						itemId={item.id}
						assetId={item.assetId}
					/>
				)}
			</CollapsableInspectorSection>
		</div>
	);
};

export const AudioInspector = React.memo(AudioInspectorUnmemoized);
