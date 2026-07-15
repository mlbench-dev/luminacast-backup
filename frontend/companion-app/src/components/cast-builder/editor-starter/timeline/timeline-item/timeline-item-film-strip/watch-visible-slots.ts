import {debounce} from '../../../utils/debounce';
import {timelineScrollContainerRef} from '../../../utils/restore-scroll-after-zoom';

const EXTRA_SLOTS_MARGIN = 30;

export const watchVisibleSlots = ({
	canvas,
	widthOfOneFrame,
	onScrollUpdate,
}: {
	canvas: HTMLCanvasElement;
	widthOfOneFrame: number;
	onScrollUpdate: () => void;
}) => {
	const getVisibleSlots = () => {
		const rect = canvas.getBoundingClientRect();
		const viewportWidth = window.innerWidth;

		const visibleLeft = Math.max(0, -rect.left);
		const visibleRight = Math.min(canvas.width, viewportWidth - rect.left);

		const firstSlot =
			Math.floor(visibleLeft / widthOfOneFrame) - EXTRA_SLOTS_MARGIN;
		const lastSlot =
			Math.floor((visibleRight + widthOfOneFrame) / widthOfOneFrame) +
			EXTRA_SLOTS_MARGIN;

		return [Math.max(0, firstSlot), Math.max(0, lastSlot)] as const;
	};

	let visibleSlots = getVisibleSlots();

	const onScroll = debounce(
		() => {
			visibleSlots = getVisibleSlots();
			onScrollUpdate();
		},
		100,
		{leading: false},
	);
	const {current: timelineScrollContainer} = timelineScrollContainerRef;

	if (!timelineScrollContainer) {
		throw new Error('Timeline scroll container ref not found');
	}

	timelineScrollContainer.addEventListener('scroll', onScroll);

	return {
		cleanup: () => {
			timelineScrollContainer.removeEventListener('scroll', onScroll);
		},
		getVisibleSlots: () => {
			return visibleSlots;
		},
	};
};
