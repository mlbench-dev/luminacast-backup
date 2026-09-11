import {useContext, useMemo} from 'react';
import {ItemSelectedForCropContext} from '../context-provider';
import {FEATURE_CROP_BACKGROUNDS} from '../flags';
import {getCropFromItem} from '../utils/get-crop-from-item';
import {getRectAfterCrop} from '../utils/get-dimensions-after-crop';
import {EditorStarterItem} from './item-type';

export const useCroppableLayer = ({
	item,
	rotation,
	opacity,
	borderRadius,
	cropBackground,
}: {
	item: EditorStarterItem;
	rotation: number;
	opacity: number;
	borderRadius: number;
	cropBackground: boolean;
}) => {
	const itemSelectedForCrop = useContext(ItemSelectedForCropContext);
	const itemIsBeingCropped = item.id === itemSelectedForCrop;

	const rectAfterCrop = useMemo(() => {
		if (cropBackground) {
			return {
				left: item.left,
				top: item.top,
				width: item.width,
				height: item.height,
			};
		}

		return getRectAfterCrop(item);
	}, [item, cropBackground]);
	const crop = useMemo(() => getCropFromItem(item), [item]);

	if (!crop) {
		throw new Error('Crop not implemented for this item type');
	}

	// "contain" items (product shots, b-roll cutaways with something behind
	// them) fit the whole asset inside the box so nothing is cropped.
	// "contain-blur" is for FULL-CANVAS primary content (stock/generated
	// b-roll, voiceover visuals) where there's nothing meaningful behind the
	// item — contain-fit the subject (nothing cropped) and fill the margin
	// with a blurred, cover-fit copy of the SAME source instead of a hard
	// black/transparent gap. Mirrors services.aspect_conform on the render
	// backend, so the preview matches what actually gets rendered instead of
	// showing a MORE zoomed-in crop than the final video will have.
	// Everything else covers the box (avatar, PIP corners).
	const fitMeta = item.metadata?.fit;
	const objectFit = fitMeta === 'contain' || fitMeta === 'contain-blur' ? 'contain' : 'cover';
	const showBlurBackdrop = fitMeta === 'contain-blur';

	const innerStyle: React.CSSProperties = useMemo(() => {
		return {
			width: item.width,
			left: cropBackground ? 0 : -(crop.cropLeft * item.width),
			top: cropBackground ? 0 : -(crop.cropTop * item.height),
			height: item.height,
			position: 'absolute',
			objectFit,
			maxWidth: 'unset',
		};
	}, [crop.cropLeft, crop.cropTop, item.height, item.width, cropBackground, objectFit]);

	// Backdrop: same box/position as innerStyle but object-fit: cover +
	// blurred + dimmed, scaled up slightly so the blurred edge (where the
	// browser samples outside the frame) never peeks in at the box edge.
	// Rendered BEHIND the real (contain-fit) element — see VideoLayer /
	// ImageLayer, which render this style on a duplicate of the same
	// source when showBlurBackdrop is true.
	const backdropStyle: React.CSSProperties | undefined = useMemo(() => {
		if (!showBlurBackdrop) return undefined;
		return {
			...innerStyle,
			objectFit: 'cover',
			filter: 'blur(24px) brightness(0.75)',
			transform: 'scale(1.12)',
			transformOrigin: 'center',
		};
	}, [showBlurBackdrop, innerStyle]);

	const outerStyle: React.CSSProperties = useMemo(() => {
		return {
			position: 'absolute',
			left: rectAfterCrop.left,
			top: rectAfterCrop.top,
			transform: `rotate(${rotation}deg)`,
			// https://www.remotion.dev/docs/editor-starter/cropping#crop-backgrounds
			opacity: cropBackground
				? 0.3
				: itemIsBeingCropped && FEATURE_CROP_BACKGROUNDS
					? 1
					: opacity,
			overflow: 'hidden',
			width: rectAfterCrop.width,
			height: rectAfterCrop.height,
			borderRadius: cropBackground ? 0 : borderRadius,
		};
	}, [
		borderRadius,
		rotation,
		opacity,
		rectAfterCrop.height,
		rectAfterCrop.left,
		rectAfterCrop.top,
		rectAfterCrop.width,
		itemIsBeingCropped,
		cropBackground,
	]);

	return useMemo(() => {
		return {
			innerStyle,
			outerStyle,
			backdropStyle,
		};
	}, [innerStyle, outerStyle, backdropStyle]);
};
