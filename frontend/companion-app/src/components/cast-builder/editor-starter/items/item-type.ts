import {AudioItem} from './audio/audio-item-type';
import {CaptionsItem} from './captions/captions-item-type';
import {GifItem} from './gif/gif-item-type';
import {ImageItem} from './image/image-item-type';
import {SolidItem} from './solid/solid-item-type';
import {TextItem} from './text/text-item-type';
import {VideoItem} from './video/video-item-type';

export type EditorStarterItem =
	| ImageItem
	| TextItem
	| VideoItem
	| SolidItem
	| CaptionsItem
	| AudioItem
	| GifItem;
