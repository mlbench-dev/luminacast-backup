import {
	BaseItem,
	CanHaveBorderRadius,
	CanHaveCrop,
	CanHaveRotation,
} from '../shared';

export type VideoItem = BaseItem &
	CanHaveBorderRadius &
	CanHaveCrop &
	CanHaveRotation & {
		type: 'video';
		videoStartFromInSeconds: number;
		decibelAdjustment: number;
		playbackRate: number;
		audioFadeInDurationInSeconds: number;
		audioFadeOutDurationInSeconds: number;
		fadeInDurationInSeconds: number;
		fadeOutDurationInSeconds: number;
		assetId: string;
		keepAspectRatio: boolean;
	};
