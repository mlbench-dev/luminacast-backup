export type TrackInsertions =
	| {
			type: 'top';
			count: number;
	  }
	| {
			type: 'bottom';
			count: number;
	  }
	| {
			type: 'between';
			trackIndex: number;
			count: number;
	  }
	| null;
