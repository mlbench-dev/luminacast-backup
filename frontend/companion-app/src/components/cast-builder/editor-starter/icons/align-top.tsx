import {AlignLeft} from './align-left';

export const AlignTop: React.FC<React.SVGProps<SVGSVGElement>> = (props) => {
	return (
		<AlignLeft
			{...props}
			style={{
				transform: 'rotate(90deg)',
			}}
		></AlignLeft>
	);
};
