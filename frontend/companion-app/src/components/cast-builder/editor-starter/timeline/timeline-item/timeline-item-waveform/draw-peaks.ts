import {decibelToGain} from '../../../utils/decibels';
import {parseColor} from '../../utils/parse-color';

const CLIPPING_COLOR = '#FF7F50';

const BAR_WIDTH = 1;
const BAR_GAP = 0;
const BAR_TOTAL = BAR_WIDTH + BAR_GAP;

export const drawPeaks = (
	canvas: HTMLCanvasElement,
	peaks: Float32Array,
	color: string,
	decibelAdjustment: number,
	width: number,
) => {
	const ctx = canvas.getContext('2d');

	if (!ctx) {
		throw new Error('Failed to get canvas context');
	}

	const height = canvas.height;
	const w = canvas.width;

	ctx.clearRect(0, 0, w, height);

	const volume = decibelToGain(decibelAdjustment);
	if (volume === 0) return;

	const [r, g, b, a] = parseColor(color);
	const [cr, cg, cb, ca] = parseColor(CLIPPING_COLOR);

	const imageData = ctx.createImageData(w, height);
	const data = imageData.data;
	const numBars = Math.ceil(width / BAR_TOTAL);

	for (let barIndex = 0; barIndex < numBars; barIndex++) {
		const x = barIndex * BAR_TOTAL;
		if (x >= w) break;

		const peakIndex = Math.floor((barIndex / numBars) * peaks.length);
		const peak = peaks[peakIndex] || 0;

		const scaledPeak = peak * volume;
		const barHeight = Math.max(0, Math.min(height, scaledPeak * height));
		if (barHeight === 0) continue;

		const barY = Math.round(height - Math.round(barHeight));
		const barEnd = height;
		const isClipping = scaledPeak > 1;
		const clipEnd = isClipping ? Math.min(barY + 2, barEnd) : barY;

		// Write clipping pixels
		for (let y = barY; y < clipEnd; y++) {
			const idx = (y * w + x) * 4;
			data[idx] = cr;
			data[idx + 1] = cg;
			data[idx + 2] = cb;
			data[idx + 3] = ca;
		}

		// Write regular pixels
		for (let y = clipEnd; y < barEnd; y++) {
			const idx = (y * w + x) * 4;
			data[idx] = r;
			data[idx + 1] = g;
			data[idx + 2] = b;
			data[idx + 3] = a;
		}
	}

	ctx.putImageData(imageData, 0, 0);
};
