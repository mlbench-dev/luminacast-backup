import React from 'react';
import {
	FEATURE_ALIGNMENT_CONTROL,
	FEATURE_COLOR_CONTROL,
	FEATURE_DIMENSIONS_CONTROL,
	FEATURE_FONT_FAMILY_CONTROL,
	FEATURE_FONT_STYLE_CONTROL,
	FEATURE_OPACITY_CONTROL,
	FEATURE_POSITION_CONTROL,
	FEATURE_ROTATION_CONTROL,
	FEATURE_TEXT_ALIGNMENT_CONTROL,
	FEATURE_TEXT_BACKGROUND_CONTROL,
	FEATURE_TEXT_DIRECTION_CONTROL,
	FEATURE_TEXT_FONT_SIZE_CONTROL,
	FEATURE_TEXT_LETTER_SPACING_CONTROL,
	FEATURE_TEXT_LINE_HEIGHT_CONTROL,
	FEATURE_TEXT_STROKE_COLOR_CONTROL,
	FEATURE_TEXT_STROKE_WIDTH_CONTROL,
	FEATURE_TEXT_VALUE_CONTROL,
	FEATURE_VISUAL_FADE_CONTROL,
} from '../flags';
import {TextItem} from '../items/text/text-item-type';
import {ColorInspector} from './color-inspector';
import {InspectorLabel} from './components/inspector-label';
import {
	CollapsableInspectorSection,
	InspectorDivider,
} from './components/inspector-section';
import {AlignmentControls} from './controls/alignment-controls';
import {DimensionsControls} from './controls/dimensions-controls';
import {FadeControls} from './controls/fade-controls';
import {FontFamilyControl} from './controls/font-family-controls/font-family-controls';
import {FontSizeControls} from './controls/font-size-controls';
import {FontStyleControls} from './controls/font-style-controls/font-style-controls';
import {LetterSpacingControls} from './controls/letter-spacing-controls';
import {LineHeightControls} from './controls/line-height-controls';
import {OpacityControls} from './controls/opacity-controls';
import {PositionControl} from './controls/position-control';
import {RotationControl} from './controls/rotation-controls';
import {StrokeWidthControls} from './controls/stroke-width-controls';
import {TextAlignmentControls} from './controls/text-alignment-controls';
import {TextBackgroundControls} from './controls/text-background-controls/text-background-controls';
import {TextDirectionControls} from './controls/text-direction-controls';
import {TextValueControls} from './controls/text-value-controls/text-value-controls';

const TextInspectorUnmemoized: React.FC<{
	item: TextItem;
}> = ({item}) => {
	return (
		<div>
			<CollapsableInspectorSection
				summary={<InspectorLabel>Layout</InspectorLabel>}
				id={`layout-${item.id}`}
				defaultOpen
			>
				{FEATURE_ALIGNMENT_CONTROL && <AlignmentControls itemId={item.id} />}
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
						itemType="text"
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
				{FEATURE_TEXT_VALUE_CONTROL && (
					<TextValueControls
						text={item.text}
						itemId={item.id}
						direction={item.direction}
						align={item.align}
					/>
				)}
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
			<CollapsableInspectorSection
				summary={<InspectorLabel>Fill</InspectorLabel>}
				id={`fill-${item.id}`}
				defaultOpen
			>
				{FEATURE_OPACITY_CONTROL && (
					<OpacityControls opacity={item.opacity} itemId={item.id} />
				)}
				{FEATURE_COLOR_CONTROL && (
					<ColorInspector
						color={item.color}
						itemId={item.id}
						colorType="color"
						accessibilityLabel="Fill color"
					/>
				)}
			</CollapsableInspectorSection>
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
			{FEATURE_TEXT_BACKGROUND_CONTROL ? (
				<>
					<InspectorDivider />
					<TextBackgroundControls item={item} />
				</>
			) : null}
			{FEATURE_VISUAL_FADE_CONTROL && (
				<>
					<InspectorDivider />
					<CollapsableInspectorSection
						id={`fade-${item.id}`}
						defaultOpen={false}
						summary={<InspectorLabel>Fade</InspectorLabel>}
					>
						<FadeControls
							fadeInDuration={item.fadeInDurationInSeconds}
							fadeOutDuration={item.fadeOutDurationInSeconds}
							itemId={item.id}
							durationInFrames={item.durationInFrames}
						/>
					</CollapsableInspectorSection>
				</>
			)}
		</div>
	);
};

export const TextInspector = React.memo(TextInspectorUnmemoized);
