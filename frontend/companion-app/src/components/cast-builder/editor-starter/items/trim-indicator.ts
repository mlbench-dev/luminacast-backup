export type ItemSide = 'left' | 'right';

export type ItemBeingTrimmed = {
	itemId: string;
	maxDurationInFrames: number | null;
	minFrom: number | null;
	side: ItemSide;
	trackIndex: number;
	top: number;
	height: number;
};
