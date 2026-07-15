/* eslint-disable prefer-rest-params */
/* eslint-disable @typescript-eslint/no-unused-vars */
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable no-var */
/* eslint-disable @typescript-eslint/no-unused-expressions */
function toVal(mix: any) {
	var k,
		y,
		str = '';

	if (typeof mix === 'string' || typeof mix === 'number') {
		str += mix;
	} else if (typeof mix === 'object') {
		if (Array.isArray(mix)) {
			var len = mix.length;
			for (k = 0; k < len; k++) {
				if (mix[k]) {
					if ((y = toVal(mix[k]))) {
						str && (str += ' ');
						str += y;
					}
				}
			}
		} else {
			for (y in mix) {
				if (mix[y]) {
					str && (str += ' ');
					str += y;
				}
			}
		}
	}

	return str;
}

export function clsx(...args: (string | null | boolean | undefined)[]) {
	var i = 0,
		tmp,
		x,
		str = '',
		len = arguments.length;
	for (; i < len; i++) {
		if ((tmp = arguments[i])) {
			if ((x = toVal(tmp))) {
				str && (str += ' ');
				str += x;
			}
		}
	}
	return str;
}
