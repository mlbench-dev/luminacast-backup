import {BaseItem, CanHaveBorderRadius, CanHaveRotation} from '../shared';

export type SolidItem = BaseItem &
	CanHaveBorderRadius &
	CanHaveRotation & {
		type: 'solid';
		color: string;
		keepAspectRatio: boolean;
		fadeInDurationInSeconds: number;
		fadeOutDurationInSeconds: number;
	};
