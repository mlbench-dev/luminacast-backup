import {useMemo} from 'react';
import {createPortal} from 'react-dom';
import {clsx} from '../../../utils/clsx';
import {calculateFadeWidthPx} from './fade-curve';
import {FadeDragHandle, getHandleLeft} from './fade-drag-handles';
import {FadeTooltip} from './fade-tooltip';
import {FadeDrag} from './use-fade-drag';

export const ItemFadeHandle: React.FC<{
	fadeDuration: number;
	totalDuration: number;
	width: number;
	showHandle: boolean;
	type: 'in' | 'out';
	fadeDrag: FadeDrag;
}> = ({fadeDuration, totalDuration, width, showHandle, type, fadeDrag}) => {
	const fadeInWidthPx = useMemo(
		() => calculateFadeWidthPx(fadeDuration, totalDuration, width),
		[fadeDuration, totalDuration, width],
	);

	const fadeInHandleClass = useMemo(
		() =>
			clsx(showHandle ? 'visible' : 'invisible', 'absolute', 'top-0', 'left-0'),
		[showHandle],
	);

	const fadeInHandleStyle = useMemo(
		() => ({
			width: 0,
			height: 0,
		}),
		[],
	);

	const {isDragging, cursorPosition, handleMouseDown, ref} = fadeDrag;

	const handlePosition = useMemo(
		() =>
			getHandleLeft({
				fadeWidthPx: fadeInWidthPx,
				currentFadeDuration: fadeDuration,
				positionProperty: type === 'in' ? 'left' : 'right',
				itemWidth: width,
			}),
		[fadeInWidthPx, fadeDuration, type, width],
	);

	return (
		<>
			<div ref={ref} className={fadeInHandleClass} style={fadeInHandleStyle}>
				<FadeDragHandle
					showHandle={showHandle}
					handlePosition={handlePosition}
					onPointerDown={handleMouseDown}
				/>
			</div>
			{isDragging &&
				cursorPosition &&
				createPortal(
					<FadeTooltip
						type={type}
						duration={fadeDuration}
						cursorPosition={cursorPosition}
						handlePosition={handlePosition}
					/>,
					document.body,
				)}
		</>
	);
};
