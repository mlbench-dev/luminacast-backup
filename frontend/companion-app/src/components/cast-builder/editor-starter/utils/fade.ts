import {EditorStarterItem} from '../items/item-type';

const canFadeVisualMap = {
	video: true,
	image: true,
	text: true,
	solid: true,
	gif: true,
	audio: false,
	captions: true,
} satisfies Record<EditorStarterItem['type'], boolean>;

export type VisuallyFadableItem = {
	[K in keyof typeof canFadeVisualMap]: (typeof canFadeVisualMap)[K] extends true
		? Extract<EditorStarterItem, {type: K}>
		: never;
}[keyof typeof canFadeVisualMap];

export const getCanFadeVisual = (
	item: EditorStarterItem,
): item is VisuallyFadableItem => {
	return canFadeVisualMap[item.type];
};

const canFadeAudioMap = {
	audio: true,
	video: true,
	captions: false,
	text: false,
	solid: false,
	gif: false,
	image: false,
} satisfies Record<EditorStarterItem['type'], boolean>;

export type AudioFadableItem = {
	[K in keyof typeof canFadeAudioMap]: (typeof canFadeAudioMap)[K] extends true
		? Extract<EditorStarterItem, {type: K}>
		: never;
}[keyof typeof canFadeAudioMap];

export const getCanFadeAudio = (
	item: EditorStarterItem,
): item is AudioFadableItem => {
	return canFadeAudioMap[item.type];
};
