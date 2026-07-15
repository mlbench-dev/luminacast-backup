// Smoke test: verifies the composition module exports the documented props
// schema and that the pure helpers behave. Runs with `npm run smoke`
// (node --test via tsx) — NO Chromium / render needed, so it is safe in CI
// and on machines without the browser deps. The full transparent-overlay
// render is exercised by the container smoke test in README.md.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
	CaptionToken,
	computeDurationInFrames,
	DEFAULT_CAPTION_COMPOSITION_PROPS,
} from './CaptionComposition.tsx';
import {
	CAPTION_COMPOSITION_ID,
	CaptionRenderProps,
	DEFAULT_CAPTION_RENDER_PROPS,
} from './Root.tsx';

const sampleTokens: CaptionToken[] = [
	{text: 'Listen', startMs: 0, endMs: 400},
	{text: 'up', startMs: 400, endMs: 700},
	{text: 'this', startMs: 700, endMs: 950},
	{text: 'works', startMs: 950, endMs: 1400},
];

test('exposes a stable composition id', () => {
	assert.equal(CAPTION_COMPOSITION_ID, 'CaptionOverlay');
});

test('default props satisfy the documented schema', () => {
	const props: CaptionRenderProps = DEFAULT_CAPTION_RENDER_PROPS;
	assert.ok(Array.isArray(props.tokens));
	assert.equal(typeof props.presetId, 'string');
	assert.equal(typeof props.pageDurationInMilliseconds, 'number');
	assert.equal(typeof props.switchCaptionsEveryMs, 'number');
	assert.equal(typeof props.width, 'number');
	assert.equal(typeof props.height, 'number');
	assert.equal(typeof props.fps, 'number');
	// Composition defaults flow from the shared composition defaults.
	assert.equal(
		props.presetId,
		DEFAULT_CAPTION_COMPOSITION_PROPS.presetId,
	);
});

test('a hand-built hormozi_bold render-props object typechecks', () => {
	const props: CaptionRenderProps = {
		tokens: sampleTokens,
		presetId: 'hormozi_bold',
		pageDurationInMilliseconds: 1200,
		switchCaptionsEveryMs: 1200,
		fontFamily: '',
		fontStyleVariant: 'normal',
		fontStyleWeight: '800',
		lineHeight: 1.2,
		letterSpacing: 0,
		maxLines: 2,
		captionWidth: 972,
		width: 1080,
		height: 1920,
		fps: 30,
	};
	assert.equal(props.presetId, 'hormozi_bold');
	assert.equal(props.tokens.length, 4);
});

test('computeDurationInFrames covers the last token plus a tail', () => {
	// last endMs = 1400ms; +200ms tail = 1600ms; at 30fps => ceil(48) = 48
	assert.equal(computeDurationInFrames(sampleTokens, 30), 48);
	// empty token list => only the tail (200ms @30fps = 6 frames).
	assert.equal(computeDurationInFrames([], 30), 6);
	// duration is always at least one frame.
	assert.ok(computeDurationInFrames([], 1) >= 1);
});
