// IndexedDB medium adapter — zero-dependency wrapper over a single object store.
//
// Why IndexedDB (not localStorage): async + non-blocking (localStorage's
// synchronous write is the known cause of the freeze when navigating back to
// home), hundreds of MB of quota, and a quota namespace separate from the
// gallery export's localStorage usage. Hand-rolled to avoid adding an `idb`
// dependency and keep the OpenNext bundle lean.
//
// Every function is fault-tolerant: when IndexedDB is unavailable (SSR, private
// mode, blocked) or any operation fails, it resolves to a safe value
// (null / [] / false) and never throws.

const DB_NAME = "infiplot";
const DB_VERSION = 1;

/** The single object store holding story records (keyPath = "id"). */
export const STORIES_STORE = "stories";

// Memoized open promise — opened once per page, reused thereafter.
let dbPromise: Promise<IDBDatabase | null> | null = null;

function isAvailable(): boolean {
  return typeof window !== "undefined" && typeof indexedDB !== "undefined";
}

function promisifyRequest<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txDone(tx: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

/** Open (and lazily create) the database. Resolves to null when IndexedDB is
 *  unavailable or the open fails/blocks — callers degrade gracefully.
 *  A transient failure (onerror, onblocked) resets the memoized promise so the
 *  next call retries rather than permanently disabling persistence for the page
 *  session. Only a successful open is cached — and even that cache is dropped if
 *  the connection later dies (onclose / onversionchange), so a post-open
 *  invalidation reopens on the next call instead of reusing a dead handle. */
export function idbReady(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  if (!isAvailable()) return Promise.resolve(null);
  dbPromise = new Promise<IDBDatabase | null>((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        try {
          const db = req.result;
          if (!db.objectStoreNames.contains(STORIES_STORE)) {
            db.createObjectStore(STORIES_STORE, { keyPath: "id" });
          }
        } catch {
          // createObjectStore failed (corrupt/quota/half-open) — the version-
          // change transaction will abort, req.onerror fires, and we resolve null
          // with the retry reset below.
        }
      };
      req.onsuccess = () => {
        const db = req.result;
        // Post-open invalidation: a successfully-opened connection can still die.
        // The browser may evict the DB under storage pressure (onclose), or
        // another tab may request a version upgrade we must yield to
        // (onversionchange). Without these handlers the memoized-but-dead db is
        // reused forever — every later transaction throws InvalidStateError,
        // which each op swallows in its try/catch, so persistence is silently
        // dead for the whole page session (exactly the "permanent disable" the
        // onerror/onblocked retry above set out to prevent, just on a later
        // branch). Dropping dbPromise lets the next call reopen.
        db.onclose = () => {
          // Connection already closed by the browser; just allow a reopen.
          dbPromise = null;
        };
        db.onversionchange = () => {
          // Another tab wants to upgrade — close first so we don't block it with
          // onblocked, then allow this tab to reopen at the new version.
          dbPromise = null;
          try {
            db.close();
          } catch {
            // best-effort
          }
        };
        resolve(db);
      };
      req.onerror = () => {
        // Transient failure — allow retry on next call.
        dbPromise = null;
        resolve(null);
      };
      req.onblocked = () => {
        // Another tab holds the connection — allow retry once it's released.
        dbPromise = null;
        resolve(null);
      };
    } catch {
      dbPromise = null;
      resolve(null);
    }
  });
  return dbPromise;
}

/** Read one record by key. Returns null when absent or unavailable. */
export async function idbGet<T>(
  storeName: string,
  key: string,
): Promise<T | null> {
  try {
    const db = await idbReady();
    if (!db) return null;
    const tx = db.transaction(storeName, "readonly");
    const result = await promisifyRequest<T>(
      tx.objectStore(storeName).get(key) as IDBRequest<T>,
    );
    return result ?? null;
  } catch {
    return null;
  }
}

/** Read every record in the store. Returns [] when empty or unavailable. */
export async function idbGetAll<T>(storeName: string): Promise<T[]> {
  try {
    const db = await idbReady();
    if (!db) return [];
    const tx = db.transaction(storeName, "readonly");
    const result = await promisifyRequest<T[]>(
      tx.objectStore(storeName).getAll() as IDBRequest<T[]>,
    );
    return result ?? [];
  } catch {
    return [];
  }
}

/** Count records in the store WITHOUT deserializing any values — the cheap way
 *  to test a capacity threshold before falling back to a full idbGetAll. Returns
 *  0 when empty or unavailable. */
export async function idbCount(storeName: string): Promise<number> {
  try {
    const db = await idbReady();
    if (!db) return 0;
    const tx = db.transaction(storeName, "readonly");
    const result = await promisifyRequest<number>(
      tx.objectStore(storeName).count() as IDBRequest<number>,
    );
    return result ?? 0;
  } catch {
    return 0;
  }
}

/** Upsert one record (keyPath "id"). Returns true on durable commit. */
export async function idbPut<T>(storeName: string, value: T): Promise<boolean> {
  try {
    const db = await idbReady();
    if (!db) return false;
    const tx = db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).put(value);
    await txDone(tx);
    return true;
  } catch {
    return false;
  }
}

/** Delete one record by key. Returns true on durable commit. */
export async function idbDelete(
  storeName: string,
  key: string,
): Promise<boolean> {
  try {
    const db = await idbReady();
    if (!db) return false;
    const tx = db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).delete(key);
    await txDone(tx);
    return true;
  } catch {
    return false;
  }
}
