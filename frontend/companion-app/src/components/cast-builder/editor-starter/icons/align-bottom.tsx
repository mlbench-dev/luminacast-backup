import {AlignRight} from './align-right';

export const AlignBottom: React.FC<React.SVGProps<SVGSVGElement>> = (props) => {
	return (
		<AlignRight
			{...props}
			style={{
				transform: 'rotate(90deg)',
			}}
		></AlignRight>
	);
};
