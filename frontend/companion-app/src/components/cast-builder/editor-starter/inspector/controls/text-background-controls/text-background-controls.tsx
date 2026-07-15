import {useCallback} from 'react';
import {TextButton} from '../../../action-row/text-button';
import {
	FEATURE_TEXT_BACKGROUND_BORDER_RADIUS_CONTROL,
	FEATURE_TEXT_BACKGROUND_HORIZONTAL_PADDING_CONTROL,
} from '../../../flags';
import {TextItem} from '../../../items/text/text-item-type';
import {
	addBackground,
	removeBackground,
} from '../../../state/actions/background';
import {changeItem} from '../../../state/actions/change-item';
import {useWriteContext} from '../../../utils/use-context';
import {ColorInspector} from '../../color-inspector';
import {InspectorLabel} from '../../components/inspector-label';
import {CollapsableInspectorSection} from '../../components/inspector-section';
import {BorderRadiusControl} from '../border-radius-controls';
import {BackgroundHorizontalPaddingControl} from './background-horizontal-padding-control';

export const TextBackgroundControls: React.FC<{
	item: TextItem;
}> = ({item}) => {
	const {setState} = useWriteContext();

	const onRemoveBackground = useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, item.id, (i) => {
					return removeBackground({item: i as TextItem});
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, item.id]);

	const onAddBackground = useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, item.id, (i) => {
					return addBackground({item: i as TextItem});
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, item.id]);

	return (
		<CollapsableInspectorSection
			summary={<InspectorLabel>Background</InspectorLabel>}
			id={`background-${item.id}`}
			defaultOpen={false}
		>
			{item.background ? (
				<>
					<ColorInspector
						color={item.background.color}
						itemId={item.id}
						colorType="backgroundColor"
						accessibilityLabel="Background color"
					/>
					<div className="flex flex-row gap-2">
						{FEATURE_TEXT_BACKGROUND_BORDER_RADIUS_CONTROL ? (
							<BorderRadiusControl
								borderRadius={item.background.borderRadius}
								borderRadiusType="background"
								itemId={item.id}
							/>
						) : null}
						{FEATURE_TEXT_BACKGROUND_HORIZONTAL_PADDING_CONTROL ? (
							<BackgroundHorizontalPaddingControl
								backgroundHorizontalPadding={
									item.background.horizontalPadding ?? 0
								}
								itemId={item.id}
							/>
						) : null}
					</div>
					<div className="h-2" />
					<TextButton onClick={onRemoveBackground}>
						Remove background
					</TextButton>
				</>
			) : (
				<TextButton onClick={onAddBackground}>Add Background</TextButton>
			)}
		</CollapsableInspectorSection>
	);
};
