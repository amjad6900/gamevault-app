CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_salt TEXT NOT NULL,
  password_verifier TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  created_at TEXT NOT NULL,
  games INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS auth_challenges (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  salt TEXT NOT NULL,
  nonce TEXT NOT NULL,
  verifier TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_challenges_expiry ON auth_challenges(expires_at);

CREATE TABLE IF NOT EXISTS auth_attempts (
  account_key TEXT NOT NULL,
  action TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_key_action_time ON auth_attempts(account_key, action, created_at);

CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  genre TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  filename TEXT NOT NULL,
  download_url TEXT,
  size_label TEXT NOT NULL DEFAULT '—',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  emoji TEXT NOT NULL DEFAULT '🎮',
  game_date TEXT NOT NULL,
  cover_blob BLOB,
  cover_type TEXT,
  downloads INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_created ON games(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_downloads ON games(downloads DESC);
