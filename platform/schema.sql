-- Apply explicitly to a new private D1 database. No public corpus tables.
CREATE TABLE IF NOT EXISTS training_jobs (
 id TEXT PRIMARY KEY,
 auth_mac TEXT NOT NULL,
 state TEXT NOT NULL,
 format TEXT NOT NULL CHECK (format IN ('txt','jsonl')),
 bytes INTEGER NOT NULL CHECK (bytes BETWEEN 1 AND 5242880),
 sha256 TEXT NOT NULL,
 object_key TEXT NOT NULL UNIQUE,
 storage TEXT NOT NULL CHECK (storage IN ('d1','r2')),
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL,
 stripe_price TEXT NOT NULL,
 amount INTEGER NOT NULL,
 stripe_session TEXT UNIQUE,
 checkout_url TEXT,
 payment_event TEXT UNIQUE,
 authorization_mode TEXT,
 message_code TEXT
);
-- Lifetime singleton: never delete/refund this claim after failure or uncertainty.
CREATE TABLE IF NOT EXISTS training_sandbox_claim (
 singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
 job_id TEXT NOT NULL UNIQUE,
 claimed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS training_jobs_expiry ON training_jobs(expires_at);
CREATE TABLE IF NOT EXISTS training_corpus_chunks (
 job_id TEXT NOT NULL REFERENCES training_jobs(id),
 ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 19),
 bytes INTEGER NOT NULL CHECK (bytes BETWEEN 1 AND 262144),
 sha256 TEXT NOT NULL,
 data BLOB NOT NULL CHECK (length(data) = bytes),
 PRIMARY KEY(job_id,ordinal)
);
