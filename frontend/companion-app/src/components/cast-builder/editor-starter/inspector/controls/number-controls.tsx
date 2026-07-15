import React, {useCallback, useEffect, useRef} from 'react';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from '../../force-specific-cursor';

function roundToStep(value: number, step: number): number {
	const rounded = Math.round(value / step) * step;
	// Fix floating point precision issues:
	const decimals = step.toString().split('.')[1]?.length || 0;
	return Number(rounded.toFixed(decimals));
}

export type NumberControlUpdateHandler = (update: {
	num: number;
	commitToUndoStack: boolean;
}) => void;

export const NumberControl: React.FC<{
	value: number;
	setValue: NumberControlUpdateHandler;
	label: React.ReactNode;
	min: number | null;
	max: number | null;
	step: number | null;
	accessibilityLabel: string;
}> = ({setValue, value, label, min, max, step, accessibilityLabel}) => {
	const ref = useRef<HTMLInputElement>(null);

	const [focused, setFocused] = React.useState(false);
	const [initialValue, setInitialValue] = React.useState(0);
	const [initialX, setInitialX] = React.useState(0);
	const [dragging, setDragging] = React.useState(false);

	const onFocus = React.useCallback(() => {
		setFocused(true);
		ref.current?.select();
	}, []);

	const onBlur = React.useCallback(() => {
		setFocused(false);
	}, []);

	const onChange = React.useCallback(
		(e: React.ChangeEvent<HTMLInputElement>) => {
			const newValue = Number(e.target.value);
			// Only call setValue if the value is a valid number
			if (!isNaN(newValue)) {
				setValue({num: newValue, commitToUndoStack: true});
			}
		},
		[setValue],
	);

	const onKeyDown = React.useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			if (e.key === 'Enter') {
				ref.current?.blur();
			}
		},
		[],
	);

	const onLabelMouseDown = useCallback(
		(e: React.MouseEvent) => {
			e.preventDefault();
			e.stopPropagation();

			setInitialValue(value);
			setInitialX(e.clientX);
			forceSpecificCursor('ew-resize');
			setDragging(true);
		},
		[value],
	);

	const calculatePosition = useCallback(
		(e: MouseEvent) => {
			const deltaX = e.clientX - initialX;
			const sensitivity = 0.1;
			const actualStep = step ?? 1;
			const rounded =
				initialValue + Math.round(deltaX * sensitivity) * actualStep;
			const roundedToStep = roundToStep(rounded, actualStep);
			const newValue = Math.min(
				Math.max(min ?? -Infinity, roundedToStep),
				max ?? Infinity,
			);
			return newValue;
		},
		[initialValue, initialX, max, min, step],
	);

	const handlePointerMove = useCallback(
		(e: MouseEvent) => {
			const newValue = calculatePosition(e);
			setValue({num: newValue, commitToUndoStack: false});
		},
		[calculatePosition, setValue],
	);

	const handlePointerUp = useCallback(
		(e: MouseEvent) => {
			const newValue = calculatePosition(e);
			setValue({num: newValue, commitToUndoStack: true});
			stopForcingSpecificCursor();
			setDragging(false);
		},
		[calculatePosition, setValue],
	);

	useEffect(() => {
		if (!dragging) {
			return;
		}

		document.addEventListener('pointermove', handlePointerMove);
		document.addEventListener('pointerup', handlePointerUp);

		return () => {
			document.removeEventListener('pointermove', handlePointerMove);
			document.removeEventListener('pointerup', handlePointerUp);
		};
	}, [dragging, handlePointerMove, handlePointerUp]);

	return (
		<>
			<div
				data-focused={focused}
				className="editor-starter-field flex flex-1 flex-row items-center"
			>
				<div
					className="cursor-ew-resize px-2 text-xs text-white/75 transition-colors select-none hover:text-white/90"
					onMouseDown={onLabelMouseDown}
				>
					{label}
				</div>
				<input
					onKeyDown={onKeyDown}
					ref={ref}
					onFocus={onFocus}
					onBlur={onBlur}
					type="number"
					min={min ?? undefined}
					max={max ?? undefined}
					className="w-full flex-1 cursor-default [appearance:textfield] bg-transparent p-1 text-xs outline-hidden [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
					value={value}
					onChange={onChange}
					aria-label={accessibilityLabel}
				/>
			</div>
		</>
	);
};
