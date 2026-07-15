import {DB_NAME, DB_OBJECT_STORE_NAME, DB_VERSION} from './constants';

let opened: Promise<IDBDatabase> | null = null;

const openIndexedDb = (): Promise<IDBDatabase> => {
	const {promise, reject, resolve} = Promise.withResolvers<IDBDatabase>();
	const rq = indexedDB.open(DB_NAME, DB_VERSION);

	rq.onupgradeneeded = (event) => {
		try {
			const db = rq.result;
			if (event.oldVersion < DB_VERSION) {
				db.createObjectStore(DB_OBJECT_STORE_NAME, {autoIncrement: false});
			} else {
				const {transaction} = event.currentTarget as IDBOpenDBRequest;
				if (!transaction) {
					throw new Error('No transaction available during upgrade');
				}

				const objectStore = transaction.objectStore(DB_OBJECT_STORE_NAME);
				if (!objectStore) {
					throw new Error('Could not access object store during upgrade');
				}

				objectStore.clear();
			}
		} catch (err) {
			reject(new Error(`Failed to upgrade database: ${err}`));
		}
	};

	rq.onsuccess = () => {
		try {
			const db = rq.result;

			resolve(db);
		} catch (err) {
			reject(new Error(`Failed to open database: ${err}`));
		}
	};

	rq.onerror = () => {
		const error = rq.error?.message ?? 'Unknown error';
		reject(new Error(`Failed to open IndexedDB: ${error}`));
	};

	rq.onblocked = () => {
		reject(new Error('Database is blocked by another connection'));
	};

	return promise;
};

const getIndexedDbInstance = (): Promise<IDBDatabase> => {
	if (opened) {
		return opened;
	}

	opened = openIndexedDb();
	return opened;
};

const prepareTransaction = async (transactionMode: IDBTransactionMode) => {
	const db = await getIndexedDbInstance();
	const {promise, reject, resolve} = Promise.withResolvers<void>();
	const transaction = db.transaction([DB_OBJECT_STORE_NAME], transactionMode);

	transaction.onerror = () => {
		reject(new Error('Transaction failed'));
	};

	transaction.onabort = () => {
		reject(new Error('Transaction aborted'));
	};

	transaction.oncomplete = () => {
		resolve();
	};

	const objectStore = transaction.objectStore(DB_OBJECT_STORE_NAME);
	return {objectStore, waitForCompletion: () => promise};
};

const waitForRequestCompletion = async <T>(transaction: IDBRequest<T>) => {
	const {promise, reject, resolve} = Promise.withResolvers<T>();
	transaction.onsuccess = () => {
		resolve(transaction.result as T);
	};
	transaction.onerror = () => {
		reject(new Error('Transaction failed'));
	};
	return promise;
};

export const cacheAssetLocally = async ({
	assetId,
	value,
}: {
	assetId: string;
	value: Blob;
}) => {
	const {objectStore, waitForCompletion} =
		await prepareTransaction('readwrite');

	const request = objectStore.put(value, assetId);
	await waitForRequestCompletion(request);
	await waitForCompletion();
	updateKeys();
};

export const deleteCachedAsset = async ({assetId}: {assetId: string}) => {
	const {objectStore, waitForCompletion} =
		await prepareTransaction('readwrite');

	const request = objectStore.delete(assetId);
	await waitForRequestCompletion(request);
	await waitForCompletion();
	updateKeys();
};

const getCachedAsset = ({
	objectStore,
	assetId,
}: {
	objectStore: IDBObjectStore;
	assetId: string;
}) => {
	const request = objectStore.get(assetId);
	return waitForRequestCompletion(request);
};

const getKeysFromObjectStore = async ({
	objectStore,
}: {
	objectStore: IDBObjectStore;
}) => {
	const request = objectStore.getAllKeys();
	await waitForRequestCompletion(request);
	return request.result as IDBValidKey[];
};

export const getObject = async ({key}: {key: string}) => {
	const {objectStore, waitForCompletion} = await prepareTransaction('readonly');

	const asset = await getCachedAsset({objectStore, assetId: key});
	await waitForCompletion();
	return asset;
};

export const getKeys = async () => {
	const {objectStore, waitForCompletion} = await prepareTransaction('readonly');

	const keys = await getKeysFromObjectStore({objectStore});
	await waitForCompletion();
	return keys;
};

let keys: IDBValidKey[] | null = null;
let keysChangedFunctions: (() => void)[] = [];

export const onKeysChanged = (fn: () => void) => {
	keysChangedFunctions.push(fn);
	return () => {
		keysChangedFunctions = keysChangedFunctions.filter((f) => f !== fn);
	};
};

const updateKeys = async () => {
	if (typeof window === 'undefined') {
		return;
	}

	keys = await getKeys();
	keysChangedFunctions.forEach((fn) => fn());
};

export const getKeysCache = () => {
	if (keys === null) {
		return null;
	}
	return keys;
};

updateKeys();
