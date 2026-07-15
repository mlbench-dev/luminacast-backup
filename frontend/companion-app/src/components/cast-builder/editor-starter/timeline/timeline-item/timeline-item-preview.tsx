import React, {ComponentProps, PropsWithChildren} from 'react';
import {ITEM_COLORS, TRACK_COLORS} from '../../constants';
import {EditorStarterItem} from '../../items/item-type';
import {CaptionsItem} from '../../items/captions/captions-item-type';
import {SolidItem} from '../../items/solid/solid-item-type';

function getItemColor(item: EditorStarterItem): string {
	const categoryColor = (item as any).metadata?.category_color as
		| string
		| undefined;
	if (categoryColor && /^#[0-9a-f]{3,8}$/i.test(categoryColor))
		return categoryColor;
	const trackType = (item as any).metadata?.track_type as string | undefined;
	if (trackType && TRACK_COLORS[trackType]) return TRACK_COLORS[trackType];
	return ITEM_COLORS[item.type] || TRACK_COLORS.default;
}

const SimplePreview = ({children, style}: ComponentProps<'div'>) => {
	return (
		<div
			className="flex h-full w-full flex-nowrap gap-1 p-1 text-xs text-white"
			style={{background: '#475569', ...style}}
		>
			{children}
		</div>
	);
};

const Text = ({children}: PropsWithChildren) => {
	return <span className="truncate">{children}</span>;
};

SimplePreview.Text = Text;

const CaptionsPreview: React.FC<{item: CaptionsItem}> = ({item}) => {
	return (
		<SimplePreview style={{background: getItemColor(item)}}>
			<SimplePreview.Text>Captions</SimplePreview.Text>
		</SimplePreview>
	);
};

const TextItemPreview: React.FC<{
	text: string;
}> = ({text}) => {
	return (
		<SimplePreview style={{background: ITEM_COLORS.text}}>
			<SimplePreview.Text>{text}</SimplePreview.Text>
		</SimplePreview>
	);
};

const ImageItemPreview: React.FC<{item: EditorStarterItem}> = ({item}) => {
	return (
		<SimplePreview style={{background: getItemColor(item)}}></SimplePreview>
	);
};

const GifItemPreview: React.FC = () => {
	return <SimplePreview style={{background: ITEM_COLORS.gif}}></SimplePreview>;
};

const VideoItemPreview: React.FC<{item: EditorStarterItem}> = ({item}) => {
	return (
		<SimplePreview
			style={{background: getItemColor(item)}}
		></SimplePreview>
	);
};

const AudioItemPreview: React.FC<{item: EditorStarterItem}> = ({item}) => {
	return (
		<SimplePreview
			style={{background: getItemColor(item)}}
		></SimplePreview>
	);
};

const SolidItemPreview: React.FC<{
	item: SolidItem;
}> = ({item}) => {
	return (
		<SimplePreview style={{background: ITEM_COLORS.solid}}>
			<div
				className={'mt-[1px] h-3 w-3 shrink-0 rounded-full'}
				style={{
					backgroundColor: item.color,
				}}
			></div>
			<SimplePreview.Text>Solid</SimplePreview.Text>
		</SimplePreview>
	);
};

export const TimelineItemPreview: React.FC<{
	item: EditorStarterItem;
}> = ({item}) => {
	// Pull the block-category color (set in editorStarterMapping). When
	// present we render a thin colored stripe along the LEFT edge of the
	// timeline strip so the user can identify the block category at a glance
	// without losing the existing item-type color (image/video/audio).
	const categoryColor: string | undefined = (item.metadata as any)?.category_color;
	const renderMode: string | undefined = (item.metadata as any)?.render_mode;
	// avatar_action (I2V between FLUX-generated scene frames) and the
	// legacy generated_video T2V both deserve a stronger visual treatment
	// so the user can tell at a glance that the strip is an AI-generated
	// clip, not a regular speaking shot. We render the same outline + tint
	// for both, but show a distinct chip label so they're not mistaken
	// ("ACTION" for avatar_action / body_motion, "AI VIDEO" for T2V).
	const isMotion = renderMode === "motion";
	const isBodyMotion = renderMode === "body_motion";
	const isHighlighted = isMotion || isBodyMotion;
	const badgeLabel: string | null = isBodyMotion
		? "Action"
		: isMotion
			? "AI Video"
			: null;

	let inner: React.ReactNode;
	if (item.type === 'text') {
		inner = <TextItemPreview text={item.text} />;
	} else if (item.type === 'image') {
		inner = <ImageItemPreview item={item} />;
	} else if (item.type === 'video') {
		inner = <VideoItemPreview item={item} />;
	} else if (item.type === 'solid') {
		inner = <SolidItemPreview item={item} />;
	} else if (item.type === 'captions') {
		inner = <CaptionsPreview item={item} />;
	} else if (item.type === 'audio') {
		inner = <AudioItemPreview item={item} />;
	} else if (item.type === 'gif') {
		inner = <GifItemPreview />;
	} else {
		throw new Error(`Unknown item type: ${JSON.stringify(item satisfies never)}`);
	}

	if (!categoryColor) return <>{inner}</>;

	return (
		<div
			style={{
				position: 'relative',
				width: '100%',
				height: '100%',
				// Highlighted blocks (avatar_motion / body_motion): full coloured
				// outline + tinted overlay so the strip is unmistakably an
				// AI-generated clip rather than a plain speaking shot.
				outline: isHighlighted ? `2px solid ${categoryColor}` : undefined,
				outlineOffset: isHighlighted ? -2 : undefined,
				boxShadow: isHighlighted ? `inset 0 0 0 9999px ${categoryColor}22` : undefined,
				borderRadius: 4,
			}}
		>
			{inner}
			{/* Left edge stripe — always present, narrower for highlighted strips
			    since the full outline already does the heavy lifting. */}
			<div
				aria-hidden
				style={{
					position: 'absolute', left: 0, top: 0, bottom: 0, width: isHighlighted ? 0 : 4,
					backgroundColor: categoryColor,
					borderTopLeftRadius: 4, borderBottomLeftRadius: 4,
				}}
			/>
			{/* Category badge — small chip in the top-left so the user can
			    read "MOTION" / "BODY MOTION" without inspecting metadata. */}
			{badgeLabel && (
				<div
					aria-hidden
					style={{
						position: 'absolute', top: 4, left: 4,
						backgroundColor: categoryColor,
						color: '#fff', fontSize: 9, fontWeight: 600,
						letterSpacing: 0.5, textTransform: 'uppercase',
						padding: '2px 6px', borderRadius: 4,
						boxShadow: '0 1px 2px rgba(0,0,0,0.4)',
						lineHeight: 1,
					}}
				>
					{badgeLabel}
				</div>
			)}
		</div>
	);
};
