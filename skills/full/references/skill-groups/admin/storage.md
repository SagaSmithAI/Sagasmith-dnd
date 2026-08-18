# Storage administration

Storage migration is restricted local administration. Inspect `storage_status`
first and run `storage_migrate` only when the reported schema requires it.

Do not expose this group to remote principals, use it as campaign recovery, or
edit the underlying SQLite, Chroma, normalized-document cache, or managed
artifact directories directly. Backward/forward schema support remains owned
by the Core migration implementation.
