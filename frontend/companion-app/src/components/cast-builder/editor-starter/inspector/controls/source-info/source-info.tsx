import {AudioItem} from '../../../items/audio/audio-item-type';
import {GifItem} from '../../../items/gif/gif-item-type';
import {ImageItem} from '../../../items/image/image-item-type';
import {VideoItem} from '../../../items/video/video-item-type';
import {secondsToTimeString} from '../../../utils/seconds-to-time-string';
import {useAssetFromItem, useAssetStatus} from '../../../utils/use-context';
import {InspectorLabel} from '../../components/inspector-label';
import {
	CollapsableInspectorSection,
	InspectorDivider,
} from '../../components/inspector-section';
import {UploadInfo} from './upload-info';

export const SourceControls: React.FC<{
	item: VideoItem | ImageItem | GifItem | AudioItem;
}> = ({item}) => {
	const asset = useAssetFromItem(item);
	const {assetStatus} = useAssetStatus();
	const currentAssetStatus = assetStatus[asset.id];

	const duration =
		'durationInSeconds' in asset ? asset.durationInSeconds : null;

	return (
		<>
			<CollapsableInspectorSection
				summary={<InspectorLabel>Source</InspectorLabel>}
				id={`source-${item.id}`}
				defaultOpen
			>
				<div className="text-xs leading-relaxed text-neutral-300">
					<div>{item.metadata?.custom_label || asset.filename}</div>
					{duration !== null && <div>{secondsToTimeString(duration)}</div>}
					{currentAssetStatus && (
						<UploadInfo asset={asset} status={currentAssetStatus} />
					)}
				</div>
			</CollapsableInspectorSection>
			<InspectorDivider />
		</>
	);
};
