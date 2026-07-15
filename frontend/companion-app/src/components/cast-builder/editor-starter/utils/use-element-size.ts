import {useCallback, useEffect, useMemo, useState} from 'react';
import {Rect} from './fit-element-size-in-container';

// If a pane has been moved, it will cause a layout shift without
// the window having been resized. Those UI elements can call this API to
// force an update

export const useElementSize = (
	ref: React.RefObject<HTMLElement | null>,
	options: {
		triggerOnWindowResize: boolean;
	},
): Rect | null => {
	const [size, setSize] = useState<Rect | null>(() => {
		if (!ref.current) {
			return null;
		}

		const rect = ref.current.getClientRects();
		if (!rect[0]) {
			return null;
		}

		return {
			width: rect[0].width as number,
			height: rect[0].height as number,
			left: rect[0].x as number,
			top: rect[0].y as number,
		};
	});

	const observer = useMemo(() => {
		if (typeof ResizeObserver === 'undefined') {
			return null;
		}

		return new ResizeObserver((entries) => {
			// The contentRect returns the width without any `scale()`'s being applied. The height is wrong
			const {target} = entries[0];
			// The clientRect returns the size with `scale()` being applied.
			const newSize = target.getClientRects();

			if (!newSize?.[0]) {
				setSize(null);
				return;
			}

			const width = newSize[0].width;

			const height = newSize[0].height;

			setSize({
				width,
				height,
				left: newSize[0].x,
				top: newSize[0].y,
			});
		});
	}, []);

	const updateSize = useCallback(() => {
		if (!ref.current) {
			return;
		}

		const rect = ref.current.getClientRects();
		if (!rect[0]) {
			setSize(null);
			return;
		}

		setSize((prevState): Rect => {
			const isSame =
				prevState &&
				prevState.width === rect[0].width &&
				prevState.height === rect[0].height &&
				prevState.left === rect[0].x &&
				prevState.top === rect[0].y;
			if (isSame) {
				return prevState;
			}

			return {
				width: rect[0].width as number,
				height: rect[0].height as number,
				left: rect[0].x as number,
				top: rect[0].y as number,
			};
		});
	}, [ref]);

	useEffect(() => {
		if (!observer) {
			return;
		}

		const {current} = ref;
		if (current) {
			observer.observe(current);
		}

		return (): void => {
			if (current) {
				observer.unobserve(current);
			}
		};
	}, [observer, ref, updateSize]);

	useEffect(() => {
		if (!options.triggerOnWindowResize) {
			return;
		}

		window.addEventListener('resize', updateSize);

		return () => {
			window.removeEventListener('resize', updateSize);
		};
	}, [options.triggerOnWindowResize, updateSize]);

	return useMemo(() => {
		if (!size) {
			return null;
		}

		return {...size, refresh: updateSize};
	}, [size, updateSize]);
};
