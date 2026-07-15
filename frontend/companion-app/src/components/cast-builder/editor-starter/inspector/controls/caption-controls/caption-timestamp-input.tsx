import {useMemo} from 'react';
import {clsx} from '../../../utils/clsx';

export const CaptionTimestampInput: React.FC<{
	onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
	onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	onFocus: () => void;
	onBlur: () => void;
	value: string;
	valid: boolean;
	type: 'start' | 'end';
}> = ({onKeyDown, onChange, onFocus, onBlur, value, valid, type}) => {
	const style = useMemo(() => {
		return {
			borderColor: !valid ? '#ef4444' : '',
			backgroundColor: !valid
				? 'rgba(239, 68, 68, 0.1)'
				: 'var(--color-editor-starter-bg)',
		};
	}, [valid]);

	return (
		<input
			onFocus={onFocus}
			onBlur={onBlur}
			aria-label={`Caption ${type} time`}
			className={clsx(
				'editor-starter-field w-24 rounded px-1.5 py-0.5 font-mono text-xs outline-none',
				!valid && 'text-red-400',
			)}
			style={style}
			type="text"
			onChange={onChange}
			value={value}
			placeholder="0:00.0"
			onKeyDown={onKeyDown}
		/>
	);
};
