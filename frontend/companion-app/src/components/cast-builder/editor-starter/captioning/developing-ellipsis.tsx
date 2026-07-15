import React, {useEffect, useState} from 'react';

export const DevelopingEllipsis: React.FC = () => {
	const [ellipsisDots, setEllipsisDots] = useState(0);

	useEffect(() => {
		const interval = setInterval(() => {
			setEllipsisDots((d) => d + 1);
		}, 1000);

		return () => {
			clearInterval(interval);
		};
	}, []);

	return <span>{'.'.repeat((ellipsisDots % 3) + 1)}</span>;
};
