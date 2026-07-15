import {useEffect} from 'react';
// Although a container has "overflow: hidden", it may still be scrolled by the browser
// if there is a textarea that would overflow the container.
// We prevent any of that scrolling and forcing the scroll position to be 0.

export const useForbidScroll = (
	ref: React.RefObject<HTMLDivElement | null>,
) => {
	useEffect(() => {
		if (!ref.current) {
			return;
		}

		const container = ref.current;

		const handleScroll = (e: Event) => {
			e.preventDefault();
			e.stopPropagation();
			container.scrollLeft = 0;
			container.scrollTop = 0;
		};

		container.addEventListener('scroll', handleScroll);

		return () => {
			container.removeEventListener('scroll', handleScroll);
		};
	}, [ref]);
};
