// SSR shim. Mirrors only the type members of the frontend's
// text-item-type that caption-page.tsx imports. The frontend's real
// module pulls in the whole editor item graph (BaseItem, shared, …),
// none of which the headless caption render needs. Keep these three
// types in sync with frontend/.../items/text/text-item-type.ts.

export type TextAlign = 'left' | 'center' | 'right';
export type TextDirection = 'ltr' | 'rtl';

export type FontStyle = {
	variant: string;
	weight: string;
};
