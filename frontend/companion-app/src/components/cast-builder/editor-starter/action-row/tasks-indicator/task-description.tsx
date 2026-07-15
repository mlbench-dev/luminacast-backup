import {clsx} from '../../utils/clsx';

export const TaskDescription: React.FC<{
	children: React.ReactNode;
	isError: boolean;
}> = ({children, isError}) => {
	return (
		<div
			className={clsx(
				'w-full flex-1 overflow-hidden text-xs text-ellipsis text-neutral-300',
				isError ? undefined : 'whitespace-nowrap',
			)}
		>
			{children}
		</div>
	);
};
