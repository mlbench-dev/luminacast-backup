import {
	BaseItem,
	CanHaveBorderRadius,
	CanHaveCrop,
	CanHaveRotation,
} from '../shared';

export type ImageItem = BaseItem &
	CanHaveBorderRadius &
	CanHaveCrop &
	CanHaveRotation & {
		type: 'image';
		assetId: string;
		keepAspectRatio: boolean;
		fadeInDurationInSeconds: number;
		fadeOutDurationInSeconds: number;
	};
