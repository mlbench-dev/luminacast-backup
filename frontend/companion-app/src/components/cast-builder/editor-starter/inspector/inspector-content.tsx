import {useItem} from '../utils/use-context';
import {AudioInspector} from './audio-inspector';
import {CaptionsInspector} from './captions-inspector';
import {GifInspector} from './gif-inspector';
import {ImgInspector} from './img-inspector';
import {SolidInspector} from './solid-inspector';
import {TextInspector} from './text-inspector';
import {VideoInspector} from './video-inspector';

export const InspectorContent: React.FC<{
	itemId: string;
}> = ({itemId}) => {
	const item = useItem(itemId);

	if (item.type === 'image') {
		return <ImgInspector item={item} />;
	}

	if (item.type === 'captions') {
		return <CaptionsInspector item={item} />;
	}

	if (item.type === 'video') {
		return <VideoInspector item={item} />;
	}

	if (item.type === 'audio') {
		return <AudioInspector item={item} />;
	}

	if (item.type === 'text') {
		return <TextInspector item={item} />;
	}

	if (item.type === 'solid') {
		return <SolidInspector item={item} />;
	}

	if (item.type === 'gif') {
		return <GifInspector item={item} />;
	}

	throw new Error(`Unknown item type: ${JSON.stringify(item satisfies never)}`);
};
