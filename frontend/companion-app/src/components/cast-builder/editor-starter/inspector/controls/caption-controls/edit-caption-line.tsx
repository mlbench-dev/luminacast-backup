import {Caption} from '@remotion/captions';
import React, {useCallback, useState} from 'react';
import {
	millisecondsToTimeString,
	timeStringToMilliseconds,
} from '../../../utils/caption-time-utils';
import {CaptionTimestampInput} from './caption-timestamp-input';

export const EditCaptionLine: React.FC<{
	caption: Caption;
	onChange: (
		text: string,
		startMs: number,
		endMs: number,
		index: number,
	) => void;
	index: number;
}> = ({caption, onChange: onChangeHandler, index}) => {
	// State for temporarily holding invalid time strings during editing
	const [startTimeInput, setStartTimeInput] = useState('');
	const [endTimeInput, setEndTimeInput] = useState('');
	const [isEditingStart, setIsEditingStart] = useState(false);
	const [isEditingEnd, setIsEditingEnd] = useState(false);

	// Compute display values based on editing state
	const startDisplayValue = isEditingStart
		? startTimeInput
		: millisecondsToTimeString(caption.startMs);
	const endDisplayValue = isEditingEnd
		? endTimeInput
		: millisecondsToTimeString(caption.endMs);

	const onTextChange: React.ChangeEventHandler<HTMLInputElement> = useCallback(
		(e) => {
			e.stopPropagation();
			// Keep current timestamp values when only text changes
			onChangeHandler(e.target.value, caption.startMs, caption.endMs, index);
		},
		[index, onChangeHandler, caption.startMs, caption.endMs],
	);

	const onStartTimeChange: React.ChangeEventHandler<HTMLInputElement> =
		useCallback(
			(e) => {
				e.stopPropagation();
				const newValue = e.target.value;
				setStartTimeInput(newValue);

				const parsedMs = timeStringToMilliseconds(newValue);
				if (parsedMs !== null) {
					onChangeHandler(caption.text, parsedMs, caption.endMs, index);
				}
			},
			[caption.text, caption.endMs, index, onChangeHandler],
		);

	const onEndTimeChange: React.ChangeEventHandler<HTMLInputElement> =
		useCallback(
			(e) => {
				e.stopPropagation();
				const newValue = e.target.value;
				setEndTimeInput(newValue);

				const parsedMs = timeStringToMilliseconds(newValue);
				if (parsedMs !== null) {
					onChangeHandler(caption.text, caption.startMs, parsedMs, index);
				}
			},
			[caption.text, caption.startMs, index, onChangeHandler],
		);

	const onKeyDown = React.useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			e.stopPropagation();
		},
		[],
	);

	const [focused, setFocused] = React.useState(false);

	const onFocus = React.useCallback(() => {
		setFocused(true);
	}, []);

	const onBlur = React.useCallback(() => {
		setFocused(false);
	}, []);

	const onTextFocus = React.useCallback(() => {
		setFocused(true);
	}, []);

	const onStartFocus = React.useCallback(() => {
		setFocused(true);
		setIsEditingStart(true);
		setStartTimeInput(millisecondsToTimeString(caption.startMs));
	}, [caption.startMs]);

	const onStartBlur = React.useCallback(() => {
		setFocused(false);
		setIsEditingStart(false);
	}, []);

	const onEndFocus = React.useCallback(() => {
		setFocused(true);
		setIsEditingEnd(true);
		setEndTimeInput(millisecondsToTimeString(caption.endMs));
	}, [caption.endMs]);

	const onEndBlur = React.useCallback(() => {
		setFocused(false);
		setIsEditingEnd(false);
	}, []);

	// Check if time inputs are valid (only when editing)
	const startTimeValid =
		!isEditingStart || timeStringToMilliseconds(startTimeInput) !== null;
	const endTimeValid =
		!isEditingEnd || timeStringToMilliseconds(endTimeInput) !== null;

	return (
		<div
			onClick={onFocus}
			onKeyDown={onKeyDown}
			data-focused={focused}
			className="group mb-1 rounded"
		>
			<div className="flex items-center gap-2 pb-1">
				<CaptionTimestampInput
					onKeyDown={onKeyDown}
					onChange={onStartTimeChange}
					onFocus={onStartFocus}
					onBlur={onStartBlur}
					value={startDisplayValue}
					valid={startTimeValid}
					type="start"
				/>

				<span className="text-xs text-neutral-300">→</span>
				<CaptionTimestampInput
					onKeyDown={onKeyDown}
					onChange={onEndTimeChange}
					onFocus={onEndFocus}
					onBlur={onEndBlur}
					value={endDisplayValue}
					valid={endTimeValid}
					type="end"
				/>
			</div>
			<input
				onFocus={onTextFocus}
				onBlur={onBlur}
				className="editor-starter-field w-full rounded px-2 py-1 text-sm text-neutral-300 placeholder-white/40 transition-colors outline-none"
				type="text"
				onChange={onTextChange}
				value={caption.text}
				placeholder="Caption text..."
				onKeyDown={onKeyDown}
			/>
		</div>
	);
};
