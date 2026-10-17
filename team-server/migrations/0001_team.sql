CREATE TABLE teams (
 id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 code_hash TEXT NOT NULL, recovery_hash TEXT NOT NULL,
 workspace TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sessions (token TEXT PRIMARY KEY, team TEXT NOT NULL REFERENCES teams(id), expires INTEGER NOT NULL);
CREATE INDEX sessions_expiry ON sessions(expires);
CREATE TABLE attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until INTEGER NOT NULL);
