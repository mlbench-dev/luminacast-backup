import {EditorStarterItem} from '../../../items/item-type';
import {getCanFadeAudio, getCanFadeVisual} from '../../../utils/fade';

type FadeProperty =
	| 'audioFadeInDurationInSeconds'
	| 'audioFadeOutDurationInSeconds'
	| 'fadeInDurationInSeconds'
	| 'fadeOutDurationInSeconds';

export const getFadeValue = ({
	item,
	fadeProperty,
}: {
	item: EditorStarterItem;
	fadeProperty: FadeProperty;
}) => {
	if (
		fadeProperty === 'audioFadeInDurationInSeconds' ||
		fadeProperty === 'audioFadeOutDurationInSeconds'
	) {
		if (getCanFadeAudio(item)) {
			return item[fadeProperty];
		}

		throw new Error(`Cannot audio fade ${item.type}`);
	}

	if (
		fadeProperty === 'fadeInDurationInSeconds' ||
		fadeProperty === 'fadeOutDurationInSeconds'
	) {
		if (getCanFadeVisual(item)) {
			return item[fadeProperty];
		}

		throw new Error(`Cannot visually fade ${item.type}`);
	}

	throw new Error('Unknown fade property:' + (fadeProperty satisfies never));
};
