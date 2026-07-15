import {useEffect, useState} from 'react';

type FadeHoverState = 'none' | 'audio-section' | 'video-section';

export const useFadeControlHoverState = (
	ref: React.RefObject<HTMLDivElement | null>,
	waveformHeight: number,
) => {
	const [fadeHoverState, setFadeHoverState] = useState<FadeHoverState>('none');

	useEffect(() => {
		const current = ref.current;

		if (!current) {
			throw new Error('Ref is not set');
		}

		const evaluateEvent = (e: PointerEvent) => {
			const {current: refCurrent} = ref;
			if (!refCurrent) {
				return;
			}

			const rect = refCurrent.getBoundingClientRect();
			const top = rect.top;
			const height = rect.height;
			const compOffset = e.pageY - top;

			const isOverWaveform = compOffset > height - waveformHeight;
			if (isOverWaveform) {
				setFadeHoverState('audio-section');
			} else {
				setFadeHoverState('video-section');
			}
		};

		const onPointerMove = (e: PointerEvent) => {
			evaluateEvent(e);
		};

		const onPointerLeave = () => {
			setFadeHoverState('none');
			current.removeEventListener('pointerleave', onPointerLeave);
			window.removeEventListener('pointermove', onPointerMove);
		};

		const onPointerEnter = (e: PointerEvent) => {
			evaluateEvent(e);
			window.addEventListener('pointermove', onPointerMove);
			current.addEventListener('pointerleave', onPointerLeave);
		};

		current.addEventListener('pointerenter', onPointerEnter);

		return () => {
			current.removeEventListener('pointerenter', onPointerEnter);
			current.removeEventListener('pointerleave', onPointerLeave);
			window.removeEventListener('pointermove', onPointerMove);
		};
	}, [ref, waveformHeight]);

	return fadeHoverState;
};
