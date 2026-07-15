import {Triangle} from '@remotion/shapes';
import {useCallback, useState} from 'react';
import {clsx} from '../../utils/clsx';

export const InspectorSection: React.FC<{
	children: React.ReactNode;
}> = ({children}) => {
	return <div className="px-4 py-3">{children}</div>;
};

export const InspectorDivider: React.FC = () => {
	return <div className="h-[1px] bg-white/10"></div>;
};

const isOpenMap: Record<string, boolean> = {};

export const CollapsableInspectorSection: React.FC<{
	children: React.ReactNode;
	summary: React.ReactNode;
	id: string;
	defaultOpen: boolean;
}> = ({children, summary, id, defaultOpen}) => {
	const [isOpen, setIsOpen] = useState(isOpenMap[id] ?? defaultOpen);

	const handleClick = useCallback(() => {
		setIsOpen((prev) => {
			const newIsOpen = !prev;
			isOpenMap[id] = newIsOpen;
			return newIsOpen;
		});
	}, [id]);

	return (
		<div className={isOpen ? 'py-3' : ''}>
			<button
				className={clsx(
					'flex w-full flex-row items-center px-4',
					isOpen ? '' : 'py-3',
				)}
				role="button"
				onClick={handleClick}
			>
				{summary}
				<div className="flex-1"></div>
				<Triangle
					direction={isOpen ? 'down' : 'right'}
					fill="currentcolor"
					className="opacity-80"
					length={8}
				/>
			</button>
			{isOpen ? (
				<div className="px-4">
					<div className="h-2"></div>
					{children}
				</div>
			) : null}
		</div>
	);
};
