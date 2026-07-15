import {CaptionsItem} from '../../items/captions/captions-item-type';
import {EditorStarterItem} from '../../items/item-type';

export const updatePageDurationInMillseconds = ({
	item,
	pageDurationInMilliseconds,
}: {
	item: EditorStarterItem;
	pageDurationInMilliseconds: number;
}): CaptionsItem => {
	if (item.type !== 'captions') {
		throw new Error('Item is not a captions');
	}

	if (item.pageDurationInMilliseconds === pageDurationInMilliseconds) {
		return item;
	}

	return {
		...item,
		pageDurationInMilliseconds,
	};
};
