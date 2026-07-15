import React from 'react';
import {ClockIcon} from '../../../icons/clock';
import {changeItem} from '../../../state/actions/change-item';
import {updatePageDurationInMillseconds} from '../../../state/actions/update-page-duration';
import {useWriteContext} from '../../../utils/use-context';
import {InspectorSubLabel} from '../../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from '../number-controls';

const PageDurationControlsUnmemoized: React.FC<{
	itemId: string;
	pageDurationInMilliseconds: number;
}> = ({pageDurationInMilliseconds, itemId}) => {
	const {setState} = useWriteContext();

	const onPageDurationInMilliseconds: NumberControlUpdateHandler =
		React.useCallback(
			({num, commitToUndoStack}) => {
				setState({
					update: (state) => {
						return changeItem(state, itemId, (i) =>
							updatePageDurationInMillseconds({
								item: i,
								pageDurationInMilliseconds: num,
							}),
						);
					},
					commitToUndoStack,
				});
			},
			[setState, itemId],
		);

	return (
		<div>
			<InspectorSubLabel>Page duration (ms)</InspectorSubLabel>
			<div className="flex w-full flex-row gap-2">
				<div className="flex-1">
					<NumberControl
						label={
							<div className="flex items-center gap-1">
								<ClockIcon className="size-3" />
							</div>
						}
						setValue={onPageDurationInMilliseconds}
						value={pageDurationInMilliseconds}
						min={200}
						max={null}
						step={100}
						accessibilityLabel="Page duration"
					/>
				</div>
			</div>
		</div>
	);
};

export const PageDurationControls = React.memo(PageDurationControlsUnmemoized);
