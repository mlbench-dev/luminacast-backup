import {Caption} from '@remotion/captions';
import React, {useCallback} from 'react';
import {CaptionAsset} from '../../../assets/assets';
import {CaptionsItem} from '../../../items/captions/captions-item-type';
import {useAssetFromAssetId, useWriteContext} from '../../../utils/use-context';
import {InspectorLabel} from '../../components/inspector-label';
import {CollapsableInspectorSection} from '../../components/inspector-section';
import {EditCaptionLine} from './edit-caption-line';

export const TokensControls: React.FC<{
	item: CaptionsItem;
}> = ({item}) => {
	const {setState} = useWriteContext();
	const captionAsset = useAssetFromAssetId(item.assetId) as CaptionAsset;
	const captions = captionAsset.captions;

	const onChange = useCallback(
		(text: string, startMs: number, endMs: number, index: number) => {
			setState({
				update: (state) => {
					const updatedCaptions = captions.map(
						(caption: Caption, idx: number) => {
							if (idx === index) {
								return {
									...caption,
									text,
									startMs,
									endMs,
								};
							}
							return caption;
						},
					);

					// Update the asset in the state
					return {
						...state,
						undoableState: {
							...state.undoableState,
							assets: {
								...state.undoableState.assets,
								[item.assetId]: {
									...state.undoableState.assets[item.assetId],
									captions: updatedCaptions,
								} as CaptionAsset,
							},
						},
					};
				},
				commitToUndoStack: true,
			});
		},
		[setState, item.assetId, captions],
	);

	return (
		<CollapsableInspectorSection
			summary={
				<div className="flex justify-between">
					<InspectorLabel>Tokens</InspectorLabel>
					<div className="w-2"></div>
					<div className="text-xs text-neutral-300">{captions.length}</div>
				</div>
			}
			id={`tokens-${item.id}`}
			defaultOpen
		>
			<div className="flex flex-col gap-2">
				{captions.map((caption: Caption, i: number) => {
					return (
						<EditCaptionLine
							key={i}
							onChange={onChange}
							index={i}
							caption={caption}
						/>
					);
				})}
			</div>

			{captions.length === 0 && (
				<div className="py-4 text-center">
					<p className="text-sm text-neutral-300">No captions</p>
				</div>
			)}
		</CollapsableInspectorSection>
	);
};
