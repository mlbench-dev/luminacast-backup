import {fitTextOnNLines} from '@remotion/layout-utils';
import {useEffect, useState} from 'react';
import {AbsoluteFill} from 'remotion';
import {useDimensions} from '../utils/use-context';

const text = 'Drop videos and images here to get started.';

export const EmptyCanvasPlaceholder = () => {
	const {compositionWidth} = useDimensions();
	const [show, setShow] = useState(false);

	useEffect(() => {
		// Cannot server-render this component
		if (typeof window === 'undefined') {
			return;
		}

		setShow(true);
	}, []);

	if (!show) {
		return null;
	}

	const {fontSize, lines} = fitTextOnNLines({
		text,
		maxLines: 2,
		maxBoxWidth: compositionWidth * 0.8,
		fontFamily: 'Arial',
	});

	return (
		<AbsoluteFill
			className="flex flex-col items-center justify-center text-center text-white/50 select-none"
			style={{
				fontSize,
			}}
		>
			{lines.map((line, index) => (
				<div key={index}>{line}</div>
			))}
		</AbsoluteFill>
	);
};
