export type Options<KeyType, ValueType> = {
	/**
	The maximum number of milliseconds an item should remain in the cache.

	@default Infinity

	By default, `maxAge` will be `Infinity`, which means that items will never expire.
	Lazy expiration occurs upon the next write or read call.

	Individual expiration of an item can be specified with the `set(key, value, {maxAge})` method.
	*/
	readonly maxAge?: number;

	/**
	The maximum number of items before evicting the least recently used items.
	*/
	readonly maxSize: number;

	/**
	Called right before an item is evicted from the cache.

	Useful for side effects or for items like object URLs that need explicit cleanup (`revokeObjectURL`).
	*/
	onEviction?: (key: KeyType, value: ValueType) => void;
};

interface CacheItem<ValueType> {
	value: ValueType;
	expiry?: number;
}

/**
Simple ["Least Recently Used" (LRU) cache](https://en.m.wikipedia.org/wiki/Cache_replacement_policies#Least_Recently_Used_.28LRU.29).

The instance is an [`Iterable`](https://developer.mozilla.org/en/docs/Web/JavaScript/Reference/Iteration_protocols) of `[key, value]` pairs so you can use it directly in a [`for…of`](https://developer.mozilla.org/en/docs/Web/JavaScript/Reference/Statements/for...of) loop.

@example
```
import QuickLRU from 'quick-lru';

const lru = new QuickLRU({maxSize: 1000});

lru.set('🦄', '🌈');

lru.has('🦄');
//=> true

lru.get('🦄');
//=> '🌈'
```
*/
export default class QuickLRU<KeyType = unknown, ValueType = unknown>
	implements Iterable<[KeyType, ValueType]>
{
	#size = 0;
	#cache = new Map<KeyType, CacheItem<ValueType>>();
	#oldCache = new Map<KeyType, CacheItem<ValueType>>();
	#maxSize: number;
	#maxAge: number;
	#onEviction?: (key: KeyType, value: ValueType) => void;

	constructor(
		options: Options<KeyType, ValueType> = {} as Options<KeyType, ValueType>,
	) {
		if (!(options.maxSize && options.maxSize > 0)) {
			throw new TypeError('`maxSize` must be a number greater than 0');
		}

		if (typeof options.maxAge === 'number' && options.maxAge === 0) {
			throw new TypeError('`maxAge` must be a number greater than 0');
		}

		this.#maxSize = options.maxSize;
		this.#maxAge = options.maxAge || Number.POSITIVE_INFINITY;
		this.#onEviction = options.onEviction;
	}

	// For tests.
	get __oldCache() {
		return this.#oldCache;
	}

	#emitEvictions(cache: Map<KeyType, CacheItem<ValueType>>): void {
		if (typeof this.#onEviction !== 'function') {
			return;
		}

		for (const [key, item] of cache) {
			this.#onEviction(key, item.value);
		}
	}

	#deleteIfExpired(key: KeyType, item: CacheItem<ValueType>): boolean {
		if (typeof item.expiry === 'number' && item.expiry <= Date.now()) {
			if (typeof this.#onEviction === 'function') {
				this.#onEviction(key, item.value);
			}

			return this.delete(key);
		}

		return false;
	}

	#getOrDeleteIfExpired(
		key: KeyType,
		item: CacheItem<ValueType>,
	): ValueType | undefined {
		const deleted = this.#deleteIfExpired(key, item);
		if (deleted === false) {
			return item.value;
		}
	}

	#getItemValue(
		key: KeyType,
		item: CacheItem<ValueType>,
	): ValueType | undefined {
		return item.expiry ? this.#getOrDeleteIfExpired(key, item) : item.value;
	}

	#peek(
		key: KeyType,
		cache: Map<KeyType, CacheItem<ValueType>>,
	): ValueType | undefined {
		const item = cache.get(key);
		return item ? this.#getItemValue(key, item) : undefined;
	}

	#set(key: KeyType, value: CacheItem<ValueType>): void {
		this.#cache.set(key, value);
		this.#size++;

		if (this.#size >= this.#maxSize) {
			this.#size = 0;
			this.#emitEvictions(this.#oldCache);
			this.#oldCache = this.#cache;
			this.#cache = new Map<KeyType, CacheItem<ValueType>>();
		}
	}

	#moveToRecent(key: KeyType, item: CacheItem<ValueType>): void {
		this.#oldCache.delete(key);
		this.#set(key, item);
	}

	*#entriesAscending(): Generator<
		[KeyType, CacheItem<ValueType>],
		void,
		unknown
	> {
		for (const item of this.#oldCache) {
			const [key, value] = item;
			if (!this.#cache.has(key)) {
				const deleted = this.#deleteIfExpired(key, value);
				if (deleted === false) {
					yield item;
				}
			}
		}

		for (const item of this.#cache) {
			const [key, value] = item;
			const deleted = this.#deleteIfExpired(key, value);
			if (deleted === false) {
				yield item;
			}
		}
	}

	get(key: KeyType): ValueType | undefined {
		if (this.#cache.has(key)) {
			const item = this.#cache.get(key);
			return item ? this.#getItemValue(key, item) : undefined;
		}

		if (this.#oldCache.has(key)) {
			const item = this.#oldCache.get(key);
			if (item && this.#deleteIfExpired(key, item) === false) {
				this.#moveToRecent(key, item);
				return item.value;
			}
		}
	}

	set(
		key: KeyType,
		value: ValueType,
		{maxAge = this.#maxAge}: {maxAge?: number} = {},
	): this {
		const expiry =
			typeof maxAge === 'number' && maxAge !== Number.POSITIVE_INFINITY
				? Date.now() + maxAge
				: undefined;

		if (this.#cache.has(key)) {
			this.#cache.set(key, {
				value,
				expiry,
			});
		} else {
			this.#set(key, {value, expiry});
		}

		return this;
	}

	has(key: KeyType): boolean {
		if (this.#cache.has(key)) {
			const item = this.#cache.get(key);
			return item ? !this.#deleteIfExpired(key, item) : false;
		}

		if (this.#oldCache.has(key)) {
			const item = this.#oldCache.get(key);
			return item ? !this.#deleteIfExpired(key, item) : false;
		}

		return false;
	}

	peek(key: KeyType): ValueType | undefined {
		if (this.#cache.has(key)) {
			return this.#peek(key, this.#cache);
		}

		if (this.#oldCache.has(key)) {
			return this.#peek(key, this.#oldCache);
		}
	}

	expiresIn(key: KeyType): number | undefined {
		const item = this.#cache.get(key) ?? this.#oldCache.get(key);
		if (item) {
			return item.expiry ? item.expiry - Date.now() : Number.POSITIVE_INFINITY;
		}
	}

	delete(key: KeyType): boolean {
		const deleted = this.#cache.delete(key);
		if (deleted) {
			this.#size--;
		}

		return this.#oldCache.delete(key) || deleted;
	}

	clear(): void {
		this.#cache.clear();
		this.#oldCache.clear();
		this.#size = 0;
	}

	resize(newSize: number): void {
		if (!(newSize && newSize > 0)) {
			throw new TypeError('`maxSize` must be a number greater than 0');
		}

		const items = [...this.#entriesAscending()];
		const removeCount = items.length - newSize;
		if (removeCount < 0) {
			this.#cache = new Map(items);
			this.#oldCache = new Map();
			this.#size = items.length;
		} else {
			if (removeCount > 0) {
				this.#emitEvictions(new Map(items.slice(0, removeCount)));
			}

			this.#oldCache = new Map(items.slice(removeCount));
			this.#cache = new Map();
			this.#size = 0;
		}

		this.#maxSize = newSize;
	}

	*keys(): IterableIterator<KeyType> {
		for (const [key] of this) {
			yield key;
		}
	}

	*values(): IterableIterator<ValueType> {
		for (const [, value] of this) {
			yield value;
		}
	}

	*[Symbol.iterator](): IterableIterator<[KeyType, ValueType]> {
		for (const item of this.#cache) {
			const [key, value] = item;
			const deleted = this.#deleteIfExpired(key, value);
			if (deleted === false) {
				yield [key, value.value];
			}
		}

		for (const item of this.#oldCache) {
			const [key, value] = item;
			if (!this.#cache.has(key)) {
				const deleted = this.#deleteIfExpired(key, value);
				if (deleted === false) {
					yield [key, value.value];
				}
			}
		}
	}

	*entriesDescending(): IterableIterator<[KeyType, ValueType]> {
		let items = [...this.#cache];
		for (let i = items.length - 1; i >= 0; --i) {
			const item = items[i];
			const [key, value] = item;
			const deleted = this.#deleteIfExpired(key, value);
			if (deleted === false) {
				yield [key, value.value];
			}
		}

		items = [...this.#oldCache];
		for (let i = items.length - 1; i >= 0; --i) {
			const item = items[i];
			const [key, value] = item;
			if (!this.#cache.has(key)) {
				const deleted = this.#deleteIfExpired(key, value);
				if (deleted === false) {
					yield [key, value.value];
				}
			}
		}
	}

	*entriesAscending(): IterableIterator<[KeyType, ValueType]> {
		for (const [key, value] of this.#entriesAscending()) {
			yield [key, value.value];
		}
	}

	get size() {
		if (!this.#size) {
			return this.#oldCache.size;
		}

		let oldCacheSize = 0;
		for (const key of this.#oldCache.keys()) {
			if (!this.#cache.has(key)) {
				oldCacheSize++;
			}
		}

		return Math.min(this.#size + oldCacheSize, this.#maxSize);
	}

	get maxSize() {
		return this.#maxSize;
	}

	entries(): IterableIterator<[KeyType, ValueType]> {
		return this.entriesAscending();
	}

	forEach(
		callbackFunction: (value: ValueType, key: KeyType, map: this) => void,
		thisArgument: unknown = this,
	): void {
		for (const [key, value] of this.entriesAscending()) {
			callbackFunction.call(thisArgument, value, key, this);
		}
	}

	get [Symbol.toStringTag]() {
		return 'QuickLRU';
	}

	toString() {
		return `QuickLRU(${this.size}/${this.maxSize})`;
	}

	[Symbol.for('nodejs.util.inspect.custom')]() {
		return this.toString();
	}
}
