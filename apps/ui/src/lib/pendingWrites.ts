export type PendingWrite = {
  id: string; path: string; identity: string; timeoutMs: number;
  kind: 'json' | 'form'; payload: string | [string, string | File][];
  uncertain?: boolean;
};

let database: Promise<IDBDatabase> | undefined;
function db(): Promise<IDBDatabase> {
  database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open('sagasmith-pending-writes', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('requests', { keyPath: 'id' });
    request.onsuccess = () => {
      request.result.onversionchange = () => { request.result.close(); database = undefined; };
      resolve(request.result);
    };
    request.onerror = () => { database = undefined; reject(request.error); };
  });
  return database;
}

export async function pendingWriteStore(action: 'list' | 'add' | 'put' | 'delete', value?: PendingWrite | string): Promise<PendingWrite[]> {
  const database = await db();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction('requests', action === 'list' ? 'readonly' : 'readwrite');
    const store = transaction.objectStore('requests');
    const request = action === 'list' ? store.getAll()
      : action === 'add' ? store.add(value)
        : action === 'put' ? store.put(value) : store.delete(value as string);
    transaction.oncomplete = () => resolve(action === 'list' ? request.result as PendingWrite[] : []);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error || new Error('Pending request storage aborted'));
  });
}
