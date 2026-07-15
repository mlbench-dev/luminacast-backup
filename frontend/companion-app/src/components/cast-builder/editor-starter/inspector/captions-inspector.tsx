import React, {useCallback, useState} from 'react';
import {AlertTriangle, Link2, RefreshCw, Loader2} from 'lucide-react';
import {toast} from 'sonner';
import {
	FEATURE_CAPTIONS_PAGE_DURATION_CONTROL,
	FEATURE_COLOR_CONTROL,
	FEATURE_DIMENSIONS_CONTROL,
	FEATURE_FONT_FAMILY_CONTROL,
	FEATURE_FONT_STYLE_CONTROL,
	FEATURE_OPACITY_CONTROL,
	FEATURE_POSITION_CONTROL,
	FEATURE_ROTATION_CONTROL,
	FEATURE_TEXT_ALIGNMENT_CONTROL,
	FEATURE_TEXT_DIRECTION_CONTROL,
	FEATURE_TEXT_FONT_SIZE_CONTROL,
	FEATURE_TEXT_LETTER_SPACING_CONTROL,
	FEATURE_TEXT_LINE_HEIGHT_CONTROL,
	FEATURE_TEXT_MAX_LINES_CONTROL,
	FEATURE_TEXT_STROKE_COLOR_CONTROL,
	FEATURE_TEXT_STROKE_WIDTH_CONTROL,
	FEATURE_TOKENS_CONTROL,
} from '../flags';
import {CaptionsItem} from '../items/captions/captions-item-type';
import {ColorInspector} from './color-inspector';
import {InspectorLabel, InspectorSubLabel} from './components/inspector-label';
import {
	CollapsableInspectorSection,
	InspectorDivider,
} from './components/inspector-section';
import {AlignmentControls} from './controls/alignment-controls';
import {PageDurationControls} from './controls/caption-controls/page-duration-controls';
import {TokensControls} from './controls/caption-controls/tokens-controls';
import {DimensionsControls} from './controls/dimensions-controls';
import {FontFamilyControl} from './controls/font-family-controls/font-family-controls';
import {FontSizeControls} from './controls/font-size-controls';
import {FontStyleControls} from './controls/font-style-controls/font-style-controls';
import {LetterSpacingControls} from './controls/letter-spacing-controls';
import {LineHeightControls} from './controls/line-height-controls';
import {MaxLinesControls} from './controls/max-lines-controls';
import {OpacityControls} from './controls/opacity-controls';
import {PositionControl} from './controls/position-control';
import {RotationControl} from './controls/rotation-controls';
import {StrokeWidthControls} from './controls/stroke-width-controls';
import {TextAlignmentControls} from './controls/text-alignment-controls';
import {TextDirectionControls} from './controls/text-direction-controls';
import {useAllItems, useAssets, useFps, useWriteContext} from '../utils/use-context';
import {useLuminacastEditor} from '../luminacast-context';
import {changeItem} from '../state/actions/change-item';
import {addCaptionAsset} from '../state/actions/add-caption-asset';
import {regenerateCaptionForBlock} from '@/lib/captionGeneration';
import type {AudioItem} from '../items/audio/audio-item-type';
import {
	CAPTION_PRESET_LIST,
	applyPresetToCaptionItem,
	type CaptionPresetId,
} from '../captioning/caption-presets';
import {DEFAULT_CAPTION_PRESET_ID} from '@/lib/captionPresets';

const CaptionsInspectorUnmemoized: React.FC<{
	item: CaptionsItem;
}> = ({item}) => {
	const {castId} = useLuminacastEditor();
	const {setState} = useWriteContext();
	const {items} = useAllItems();
	const {assets} = useAssets();
	const {fps} = useFps();
	const [regenerating, setRegenerating] = useState(false);

	const sourceAudioId = item.metadata?.source_audio_id;
	const lockedToSource = item.metadata?.locked_to_source !== false; // default true
	const isStale = item.metadata?.stale === true;
	const blockId = item.metadata?.block_id;

	// Caption preset state for THIS block. presetId comes from
	// metadata.caption_preset; presetOverride is a sticky flag that, when
	// true, makes the global "Apply to all" skip this item.
	const currentPresetId =
		(item.metadata?.caption_preset as CaptionPresetId | undefined) ||
		DEFAULT_CAPTION_PRESET_ID;
	const presetOverride = item.metadata?.preset_override === true;

	const handleChangePreset = useCallback(
		(presetId: CaptionPresetId) => {
			setState({
				update: (state) =>
					changeItem(state, item.id, (i) => {
						const next = applyPresetToCaptionItem(i as CaptionsItem, presetId);
						return {
							...next,
							metadata: {
								...next.metadata,
								// Selecting a preset on a single block does NOT auto-flip
								// the override flag — the user opts into that explicitly
								// via the checkbox below.
								preset_override: i.metadata?.preset_override ?? false,
							},
						};
					}),
				commitToUndoStack: true,
			});
		},
		[item.id, setState],
	);

	const handleToggleOverride = useCallback(() => {
		setState({
			update: (state) =>
				changeItem(state, item.id, (i) => ({
					...i,
					metadata: {
						...i.metadata,
						preset_override: !presetOverride,
					},
				})),
			commitToUndoStack: true,
		});
	}, [item.id, presetOverride, setState]);

	// Resolve source audio element name
	const sourceAudioItem = sourceAudioId ? items[sourceAudioId] : null;
	const sourceAudioName = sourceAudioItem
		? `Audio (Block ${sourceAudioItem.metadata?.block_id?.slice(0, 8) ?? 'unknown'})`
		: null;

	/** Toggle locked_to_source */
	const handleToggleLock = useCallback(() => {
		setState({
			update: (state) =>
				changeItem(state, item.id, (i) => ({
					...i,
					metadata: {
						...i.metadata,
						locked_to_source: !lockedToSource,
					},
				})),
			commitToUndoStack: true,
		});
	}, [item.id, lockedToSource, setState]);

	/** Manual regenerate captions */
	const handleRegenerate = useCallback(async () => {
		if (!sourceAudioId || !blockId || regenerating) return;

		const audioItem = items[sourceAudioId];
		if (!audioItem || audioItem.type !== 'audio') {
			toast.error('Source audio element not found');
			return;
		}

		const audioAsset = assets[(audioItem as AudioItem).assetId];
		const audioUrl = audioAsset?.remoteUrl;
		if (!audioUrl) {
			toast.error('Audio URL not available');
			return;
		}

		setRegenerating(true);
		try {
			const result = await regenerateCaptionForBlock(
				castId,
				audioItem as AudioItem,
				audioUrl,
				blockId,
				fps,
			);

			if (result && result.captions.length > 0) {
				setState({
					update: (state) => {
						const {state: stateWithAsset, asset: captionAsset} =
							addCaptionAsset({
								state,
								captions: result.captions,
								filename: `captions_${blockId}.srt`,
							});

						const existingCaption =
							stateWithAsset.undoableState.items[item.id];
						if (!existingCaption) return stateWithAsset;

						return {
							...stateWithAsset,
							undoableState: {
								...stateWithAsset.undoableState,
								items: {
									...stateWithAsset.undoableState.items,
									[item.id]: {
										...existingCaption,
										assetId: captionAsset.id,
										metadata: {
											...existingCaption.metadata,
											stale: false,
										},
									},
								},
							},
						};
					},
					commitToUndoStack: true,
				});
				toast.success('Captions regenerated');
			} else {
				toast.error('Caption generation returned no results');
			}
		} catch (err) {
			console.error('Caption regeneration failed:', err);
			toast.error('Caption regeneration failed');
		} finally {
			setRegenerating(false);
		}
	}, [sourceAudioId, blockId, regenerating, items, assets, castId, fps, item.id, setState]);

	return (
		<div>
			{/* Caption preset section — per-block selector + lock toggle. */}
			<CollapsableInspectorSection
				summary={<InspectorLabel>Caption Style</InspectorLabel>}
				id={`caption-style-${item.id}`}
				defaultOpen
			>
				<InspectorSubLabel>Preset</InspectorSubLabel>
				<select
					value={currentPresetId}
					onChange={(e) =>
						handleChangePreset(e.target.value as CaptionPresetId)
					}
					className="mt-1 w-full rounded border border-white/10 bg-neutral-800 px-2 py-1.5 text-xs text-white focus:border-white/30 focus:outline-none"
				>
					{CAPTION_PRESET_LIST.map((p) => (
						<option key={p.id} value={p.id}>
							{p.name}
						</option>
					))}
				</select>

				<label className="mt-2 flex cursor-pointer items-center gap-2 text-xs text-neutral-300">
					<input
						type="checkbox"
						checked={presetOverride}
						onChange={handleToggleOverride}
						className="h-3.5 w-3.5 rounded border-neutral-600 bg-transparent text-blue-500 focus:ring-0 focus:ring-offset-0"
					/>
					Lock this block&apos;s style (skip Apply to all)
				</label>
				{presetOverride && (
					<p className="mt-1 text-[10px] text-white/40">
						Fine-tune font, color, stroke below — they will not be
						overwritten by the style bar.
					</p>
				)}
			</CollapsableInspectorSection>
			<InspectorDivider />

			{/* Phase 2.6.5 — Source linkage section */}
			{sourceAudioId && (
				<>
					<CollapsableInspectorSection
						summary={<InspectorLabel>Source Linkage</InspectorLabel>}
						id={`source-linkage-${item.id}`}
						defaultOpen
					>
						{/* Linked to */}
						<div className="flex items-center gap-1.5 text-xs text-neutral-300">
							<Link2 className="h-3 w-3 shrink-0 text-blue-400" />
							<span>Linked to: {sourceAudioName ?? 'Unknown'}</span>
						</div>

						{/* Lock toggle */}
						<label className="mt-2 flex cursor-pointer items-center gap-2 text-xs text-neutral-300">
							<input
								type="checkbox"
								checked={lockedToSource}
								onChange={handleToggleLock}
								className="h-3.5 w-3.5 rounded border-neutral-600 bg-transparent text-blue-500 focus:ring-0 focus:ring-offset-0"
							/>
							Auto-regenerate when audio changes
						</label>

						{/* Stale warning */}
						{isStale && (
							<div className="mt-2 flex items-start gap-1.5 rounded bg-yellow-900/30 px-2 py-1.5 text-xs text-yellow-300">
								<AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
								<div>
									<span>Source audio changed.</span>
									<button
										className="ml-1 font-medium underline hover:text-yellow-100"
										onClick={handleRegenerate}
										disabled={regenerating}
									>
										{regenerating ? 'Regenerating...' : 'Regenerate'}
									</button>
								</div>
							</div>
						)}

						{/* Regenerate now button */}
						<button
							className="mt-2 flex w-full items-center justify-center gap-1.5 rounded bg-neutral-700 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-neutral-600 disabled:cursor-not-allowed disabled:opacity-50"
							onClick={handleRegenerate}
							disabled={regenerating}
						>
							{regenerating ? (
								<Loader2 className="h-3.5 w-3.5 animate-spin" />
							) : (
								<RefreshCw className="h-3.5 w-3.5" />
							)}
							{regenerating ? 'Regenerating...' : 'Regenerate now'}
						</button>
					</CollapsableInspectorSection>
					<InspectorDivider />
				</>
			)}

			<CollapsableInspectorSection
				summary={<InspectorLabel>Layout</InspectorLabel>}
				id={`layout-${item.id}`}
				defaultOpen
			>
				<AlignmentControls itemId={item.id} />
				{FEATURE_POSITION_CONTROL && <PositionControl itemId={item.id} />}
				{FEATURE_DIMENSIONS_CONTROL && <DimensionsControls itemId={item.id} />}
				{FEATURE_ROTATION_CONTROL && (
					<RotationControl rotation={item.rotation} itemId={item.id} />
				)}
			</CollapsableInspectorSection>
			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>Typography</InspectorLabel>}
				id={`typography-${item.id}`}
				defaultOpen
			>
				{FEATURE_FONT_FAMILY_CONTROL && (
					<FontFamilyControl fontFamily={item.fontFamily} itemId={item.id} />
				)}
				{FEATURE_FONT_STYLE_CONTROL && (
					<FontStyleControls
						fontFamily={item.fontFamily}
						fontStyle={item.fontStyle}
						itemId={item.id}
					/>
				)}
				{FEATURE_TEXT_FONT_SIZE_CONTROL && (
					<FontSizeControls
						fontSize={item.fontSize}
						itemId={item.id}
						itemType="captions"
					/>
				)}

				<div className="flex flex-row gap-2">
					{FEATURE_TEXT_LINE_HEIGHT_CONTROL && (
						<LineHeightControls lineHeight={item.lineHeight} itemId={item.id} />
					)}
					{FEATURE_TEXT_LETTER_SPACING_CONTROL && (
						<LetterSpacingControls
							letterSpacing={item.letterSpacing}
							itemId={item.id}
						/>
					)}
				</div>
				<div className="flex flex-row gap-2">
					{FEATURE_TEXT_ALIGNMENT_CONTROL && (
						<TextAlignmentControls align={item.align} itemId={item.id} />
					)}
					{FEATURE_TEXT_DIRECTION_CONTROL && (
						<TextDirectionControls
							direction={item.direction}
							itemId={item.id}
						/>
					)}
				</div>
			</CollapsableInspectorSection>
			<InspectorDivider />
			{FEATURE_OPACITY_CONTROL && (
				<CollapsableInspectorSection
					summary={<InspectorLabel>Fill</InspectorLabel>}
					id={`fill-${item.id}`}
					defaultOpen
				>
					{FEATURE_OPACITY_CONTROL && (
						<OpacityControls opacity={item.opacity} itemId={item.id} />
					)}
					<div className="flex flex-row gap-2">
						{FEATURE_COLOR_CONTROL && (
							<ColorInspector
								color={item.color}
								itemId={item.id}
								colorType="color"
								accessibilityLabel="Fill color"
							/>
						)}
						{FEATURE_COLOR_CONTROL && (
							<ColorInspector
								color={item.highlightColor}
								itemId={item.id}
								colorType="highlightColor"
								accessibilityLabel="Highlight color"
							/>
						)}
					</div>
				</CollapsableInspectorSection>
			)}
			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>Stroke</InspectorLabel>}
				id={`stroke-${item.id}`}
				defaultOpen={false}
			>
				{FEATURE_TEXT_STROKE_WIDTH_CONTROL && (
					<StrokeWidthControls
						strokeWidth={item.strokeWidth}
						itemId={item.id}
					/>
				)}
				{FEATURE_TEXT_STROKE_COLOR_CONTROL && (
					<ColorInspector
						color={item.strokeColor}
						itemId={item.id}
						colorType="strokeColor"
						accessibilityLabel="Stroke color"
					/>
				)}
			</CollapsableInspectorSection>
			<InspectorDivider />
			<CollapsableInspectorSection
				summary={<InspectorLabel>Captions</InspectorLabel>}
				id={`captions-${item.id}`}
				defaultOpen={false}
			>
				{FEATURE_CAPTIONS_PAGE_DURATION_CONTROL && (
					<PageDurationControls
						pageDurationInMilliseconds={item.pageDurationInMilliseconds}
						itemId={item.id}
					/>
				)}
				{FEATURE_TEXT_MAX_LINES_CONTROL && (
					<MaxLinesControls maxLines={item.maxLines} itemId={item.id} />
				)}
			</CollapsableInspectorSection>
			<InspectorDivider />
			{FEATURE_TOKENS_CONTROL && <TokensControls item={item} />}
		</div>
	);
};

export const CaptionsInspector = React.memo(CaptionsInspectorUnmemoized);
