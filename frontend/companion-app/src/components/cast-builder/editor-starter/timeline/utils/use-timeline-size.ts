import {useContext} from 'react';
import {TimelineSizeContext} from '../timeline-size-provider';

export const useTimelineSize = () => {
	const state = useContext(TimelineSizeContext);

	if (!state) {
		throw new Error(
			'useTimelineSize must be used within a TimelineSizeProvider',
		);
	}

	return state;
};
