import {useEffect, useLayoutEffect} from 'react';

const scrollPositions: Record<string, number> = {};

export const useInspectorScrollRestoration = (
	ref: React.RefObject<HTMLDivElement | null>,
	selectedItems: string[],
) => {
	useEffect(() => {
		const current = ref.current;
		if (!current) {
			return;
		}
		if (selectedItems.length !== 1) {
			return;
		}

		const onScroll = () => {
			scrollPositions[selectedItems[0]] = ref.current?.scrollTop ?? 0;
		};

		current.addEventListener('scroll', onScroll, {passive: true});

		return () => {
			current.removeEventListener('scroll', onScroll);
		};
	}, [ref, selectedItems]);

	useLayoutEffect(() => {
		const pos = scrollPositions[selectedItems[0]] ?? 0;
		ref.current?.scrollTo({top: pos});
	}, [ref, selectedItems]);
};
