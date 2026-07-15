import React, {useRef} from 'react';

interface SliderProps {
	value: number;
	onValueChange: (value: number, commitToUndoStack: boolean) => void;
	min?: number;
	max?: number;
	step?: number;
	className?: string;
	title: string;
}

export const Slider: React.FC<SliderProps> = ({
	value,
	onValueChange,
	min = 0,
	max = 100,
	step = 1,
	className = '',
	title,
}) => {
	const ref = useRef<HTMLInputElement>(null);
	const handleChange = React.useCallback(
		(e: React.ChangeEvent<HTMLInputElement>) => {
			onValueChange(Number(e.target.value), false);
		},
		[onValueChange],
	);

	const handlePointerDown = React.useCallback(() => {
		window.addEventListener(
			'pointerup',
			() => {
				onValueChange(Number(ref.current?.value), true);
			},
			{once: true},
		);
	}, [onValueChange]);

	const percentage = ((value - min) / (max - min)) * 100;

	return (
		<div
			className={`relative flex w-full touch-none items-center select-none ${className}`}
		>
			<input
				type="range"
				min={min}
				max={max}
				step={step}
				value={value}
				ref={ref}
				onChange={handleChange}
				onPointerDown={handlePointerDown}
				title={title}
				aria-label={title}
				className="slider-thumb relative mt-2 mb-2 h-2 w-full cursor-pointer appearance-none rounded-full bg-white/20 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-white/20 focus-visible:ring-offset-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
				style={{
					background: `linear-gradient(to right, rgba(255, 255, 255, 0.5) 0%, rgba(255, 255, 255, 0.5) ${percentage}%, rgba(255, 255, 255, 0.2) ${percentage}%, rgba(255, 255, 255, 0.2) 100%)`,
				}}
			/>
			<style
				dangerouslySetInnerHTML={{
					__html: `
					.slider-thumb::-webkit-slider-thumb {
						appearance: none;
						height: 16px;
						width: 16px;
						border-radius: 50%;
						background: white;
						cursor: pointer;
						transition: all 0.2s ease;
						box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
					}
					.slider-thumb::-webkit-slider-thumb:hover {
						background: rgba(255, 255, 255, 0.9);
						transform: scale(1.1);
					}
					.slider-thumb::-moz-range-thumb {
						height: 16px;
						width: 16px;
						border-radius: 50%;
						background: white;
						cursor: pointer;
						border: none;
						transition: all 0.2s ease;
						box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
					}
					.slider-thumb::-moz-range-thumb:hover {
						background: rgba(255, 255, 255, 0.9);
						transform: scale(1.1);
					}
				`,
				}}
			/>
		</div>
	);
};
