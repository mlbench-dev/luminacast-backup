import React, {memo} from 'react';
import {getAssetFromItem} from '../../assets/utils';
import {Link} from '../../icons/link';
import {changeItem} from '../../state/actions/change-item';
import {updateItemHeight} from '../../state/actions/update-item-dimensions';
import {
	getKeepAspectRatio,
	getOriginalAspectRatio,
} from '../../utils/aspect-ratio';
import {clsx} from '../../utils/clsx';
import {useWriteContext} from '../../utils/use-context';
import {InspectorIconButton} from '../components/inspector-icon-button';

const KeepAspectRatioControlsUnmemoized = ({
	keepAspectRatio,
	itemId,
}: {
	keepAspectRatio: boolean;
	itemId: string;
}) => {
	const {setState} = useWriteContext();

	const onKeepAspectRatioChange = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (item) => {
					const newKeepAspectRatio = !getKeepAspectRatio(item);
					if (!newKeepAspectRatio) {
						return {
							...item,
							keepAspectRatio: newKeepAspectRatio,
						} as typeof item;
					}

					// Enabling aspect ratio - adjust dimensions to match original aspect ratio
					const asset = getAssetFromItem({
						item: item,
						assets: state.undoableState.assets,
					});
					const aspectRatio = getOriginalAspectRatio({item: item, asset});
					const newHeight = Math.round(item.width / aspectRatio);

					// Update both keepAspectRatio and height
					const updatedItem = updateItemHeight({
						item: {
							...item,
							keepAspectRatio: newKeepAspectRatio,
						} as typeof item,
						height: newHeight,
						stopTextEditing: true,
					});
					return updatedItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	return (
		<div
			className={clsx(
				'editor-starter-field hover:border-transparent',
				keepAspectRatio && 'bg-editor-starter-accent/10',
			)}
		>
			<InspectorIconButton
				className={clsx(
					'flex h-full w-8 flex-1 items-center justify-center',
					keepAspectRatio && 'text-editor-starter-accent',
				)}
				aria-label="Keep Aspect Ratio"
				onClick={onKeepAspectRatioChange}
			>
				<Link height={16} width={16}></Link>
			</InspectorIconButton>
		</div>
	);
};

export const KeepAspectRatioControls = memo(KeepAspectRatioControlsUnmemoized);
