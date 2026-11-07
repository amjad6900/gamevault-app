const encoder = new TextEncoder();
const MAX_JSON_BYTES = 900_000;
const MAX_COVER_BYTES = 512 * 1024;
const SESSION_SECONDS = 24 * 60 * 60;

class ClientInputError extends Error {}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      ...headers,
    },
  });
}

function error(message, status = 400) {
  return json({ success: false, error: message }, status);
}

function randomHex(bytes = 32) {
  const value = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(value, (part) => part.toString(16).padStart(2, "0")).join("");
}

function hexBytes(value) {
  if (typeof value !== "string" || !/^(?:[a-f\d]{2})+$/i.test(value)) return null;
  return Uint8Array.from(value.match(/.{2}/g), (part) => Number.parseInt(part, 16));
}

async function sha256Hex(value) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return Array.from(new Uint8Array(digest), (part) => part.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(keyHex, messageHex) {
  const keyBytes = hexBytes(keyHex);
  const messageBytes = hexBytes(messageHex);
  if (!keyBytes || !messageBytes) return "";
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, messageBytes);
  return Array.from(new Uint8Array(signature), (part) => part.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(left, right) {
  const a = hexBytes(left);
  const b = hexBytes(right);
  if (!a || !b || a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a[index] ^ b[index];
  return difference === 0;
}

function database(env) {
  if (!env.DATABASE) throw new Error("Base D1 absente : lance d’abord la configuration Cloudflare gratuite.");
  return env.DATABASE;
}

async function readJson(request) {
  const length = Number(request.headers.get("Content-Length") || 0);
  if (length > MAX_JSON_BYTES) throw new ClientInputError("La requête dépasse la limite de 900 Ko.");
  if (!request.body) return {};
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_JSON_BYTES) {
        await reader.cancel();
        throw new ClientInputError("La requête dépasse la limite de 900 Ko.");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const text = new TextDecoder().decode(bytes);
  try { return JSON.parse(text || "{}"); }
  catch { throw new ClientInputError("JSON invalide."); }
}

function safeUser(row) {
  return {
    id: row.id,
    name: row.name,
    email: row.email,
    role: row.role,
    date: row.created_at,
    games: Number(row.games || 0),
  };
}

function displayDate() {
  const now = new Date();
  return `${new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris" }).format(now)} ${new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit", hour12: false }).format(now)}`;
}

async function parseUser(request, env) {
  const header = request.headers.get("Authorization") || "";
  if (!header.startsWith("Bearer ")) return null;
  const token = header.slice(7).trim();
  if (!/^[a-f\d]{64}$/i.test(token)) return null;
  const tokenHash = await sha256Hex(token);
  const row = await database(env).prepare(
    "SELECT u.id, u.name, u.email, u.role, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?"
  ).bind(tokenHash, Math.floor(Date.now() / 1000)).first();
  if (!row) return null;
  return { ...row, games: 0, tokenHash };
}

async function requireUser(request, env, roles = null) {
  const user = await parseUser(request, env);
  if (!user) return { response: error("Authentification requise.", 401) };
  if (roles && !roles.includes(user.role)) return { response: error("Action non autorisée.", 403) };
  return { user };
}

async function rateLimit(db, request, email, action, max) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = await sha256Hex(`${ip}\u0000${String(email || "").trim().toLowerCase()}`);
  const now = Math.floor(Date.now() / 1000);
  await db.prepare("DELETE FROM auth_attempts WHERE created_at < ?").bind(now - 1800).run();
  const result = await db.prepare(
    "SELECT COUNT(*) AS total FROM auth_attempts WHERE account_key = ? AND action = ? AND created_at > ?"
  ).bind(key, action, now - 900).first();
  if (Number(result?.total || 0) >= max) return false;
  await db.prepare("INSERT INTO auth_attempts (account_key, action, created_at) VALUES (?, ?, ?)").bind(key, action, now).run();
  return true;
}

function serializeGame(row) {
  return {
    id: row.id,
    titre: row.title,
    genre: row.genre,
    description: row.description,
    fichier: row.download_url || row.filename,
    taille: row.size_label,
    emoji: row.emoji,
    date: row.game_date,
    image: row.cover_blob ? row.id : "",
    telechargements: Number(row.downloads || 0),
  };
}

async function listGames(db) {
  const result = await db.prepare(
    "SELECT id, title, genre, description, filename, download_url, size_label, emoji, game_date, cover_blob, downloads FROM games ORDER BY created_at DESC"
  ).all();
  return (result.results || []).map(serializeGame);
}

async function getStats(db) {
  const row = await db.prepare(
    "SELECT COUNT(*) AS total_games, COALESCE(SUM(downloads), 0) AS total_downloads, COALESCE(SUM(size_bytes), 0) AS total_size FROM games"
  ).first();
  const bytes = Number(row?.total_size || 0);
  const totalSize = bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} Ko` : `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
  const games = await db.prepare("SELECT title AS titre, downloads AS telechargements FROM games ORDER BY downloads DESC, title LIMIT 8").all();
  return {
    success: true,
    total_jeux: Number(row?.total_games || 0),
    total_telechargements: Number(row?.total_downloads || 0),
    total_size: totalSize,
    jeux_stats: games.results || [],
  };
}

function hostedDisabled(message) {
  return error(message, 404);
}

async function handleApi(request, env, ctx, url) {
  const db = database(env);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/health" && method === "GET") {
    return json({ success: true, app: "GameVault", version: "4.2.0", ready: true, hosted: true, serverless: true, storage_limit_bytes: 5 * 1024 * 1024 * 1024 });
  }

  if (path === "/api/roblox/health" && method === "GET") {
    return json({ success: true, connected: true, bridge: "GameVault", version: "4.2.0", hosted: true });
  }

  if (path === "/api/auth/challenge" && method === "POST") {
    const input = await readJson(request);
    const email = String(input.email || "").trim().toLowerCase();
    if (!email || email.length > 240) return error("Adresse e-mail invalide.", 400);
    if (!await rateLimit(db, request, email, "login", 10)) return error("Trop de tentatives. Réessaie dans 15 minutes.", 429);
    const now = Math.floor(Date.now() / 1000);
    await db.prepare("DELETE FROM auth_challenges WHERE expires_at < ?").bind(now).run();
    await db.prepare("DELETE FROM sessions WHERE expires_at < ?").bind(now).run();
    const account = await db.prepare("SELECT id, password_salt, password_verifier FROM users WHERE email = ?").bind(email).first();
    const challengeId = randomHex(16);
    const nonce = randomHex(32);
    const salt = account?.password_salt || randomHex(16);
    const verifier = account?.password_verifier || randomHex(32);
    const expires = Math.floor(Date.now() / 1000) + 300;
    await db.prepare(
      "INSERT INTO auth_challenges (id, email, user_id, salt, nonce, verifier, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(challengeId, email, account?.id || null, salt, nonce, verifier, expires).run();
    return json({ success: true, challenge_id: challengeId, salt, nonce });
  }

  if (path === "/api/login" && method === "POST") {
    const input = await readJson(request);
    const email = String(input.email || "").trim().toLowerCase();
    const challengeId = String(input.challenge_id || "");
    const challenge = await db.prepare(
      "SELECT id, user_id, nonce, verifier, expires_at FROM auth_challenges WHERE id = ? AND email = ?"
    ).bind(challengeId, email).first();
    if (!challenge || Number(challenge.expires_at) < Math.floor(Date.now() / 1000)) {
      if (challenge) await db.prepare("DELETE FROM auth_challenges WHERE id = ?").bind(challengeId).run();
      return error("Email ou mot de passe incorrect.", 401);
    }
    const expected = await hmacHex(challenge.verifier, challenge.nonce);
    await db.prepare("DELETE FROM auth_challenges WHERE id = ?").bind(challengeId).run();
    if (!challenge.user_id || !constantTimeEqual(expected, String(input.proof || ""))) {
      return error("Email ou mot de passe incorrect.", 401);
    }
    const account = await db.prepare("SELECT id, name, email, role, created_at FROM users WHERE id = ?").bind(challenge.user_id).first();
    if (!account) return error("Email ou mot de passe incorrect.", 401);
    const token = randomHex(32);
    const tokenHash = await sha256Hex(token);
    const expires = Math.floor(Date.now() / 1000) + SESSION_SECONDS;
    await db.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)").bind(tokenHash, account.id, expires).run();
    return json({ success: true, user: safeUser(account), token });
  }

  if (path === "/api/register" && method === "POST") {
    const input = await readJson(request);
    const name = String(input.name || "").trim().slice(0, 120);
    const email = String(input.email || "").trim().toLowerCase();
    const salt = String(input.salt || "").toLowerCase();
    const verifier = String(input.verifier || "").toLowerCase();
    if (!name || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 240) return error("Nom ou adresse e-mail invalide.", 400);
    if (!hexBytes(salt) || salt.length !== 32 || !hexBytes(verifier) || verifier.length !== 64) return error("Données de mot de passe invalides.", 400);
    if (!await rateLimit(db, request, email, "register", 5)) return error("Trop de créations pour cette adresse. Réessaie plus tard.", 429);
    const id = crypto.randomUUID();
    const date = displayDate();
    try {
      await db.prepare(
        "INSERT INTO users (id, name, email, password_salt, password_verifier, role, created_at) VALUES (?, ?, ?, ?, ?, 'user', ?)"
      ).bind(id, name, email, salt, verifier, date).run();
    } catch (cause) {
      if (String(cause?.message || "").toLowerCase().includes("unique")) return error("Email déjà utilisé.", 409);
      throw cause;
    }
    const token = randomHex(32);
    await db.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?) ")
      .bind(await sha256Hex(token), id, Math.floor(Date.now() / 1000) + SESSION_SECONDS).run();
    return json({ success: true, user: { id, name, email, role: "user", date, games: 0 }, token }, 201);
  }

  if (path === "/api/logout" && method === "POST") {
    const user = await parseUser(request, env);
    if (user?.tokenHash) await db.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(user.tokenHash).run();
    return json({ success: true });
  }

  if (path === "/api/catalogue" && method === "GET") return json({ success: true, jeux: await listGames(db) });
  if (path === "/api/roblox/catalogue" && method === "GET") {
    const games = await listGames(db);
    return json({ success: true, games: games.filter((game) => /roblox/i.test(game.genre) || /\.rbxlx?$/i.test(game.fichier)) });
  }
  if (path === "/api/stats" && method === "GET") return json(await getStats(db));

  if (path.startsWith("/api/download/") && method === "GET") {
    const id = decodeURIComponent(path.slice("/api/download/".length));
    const game = await db.prepare("SELECT id, filename, download_url FROM games WHERE id = ?").bind(id).first();
    if (!game) return error("Jeu introuvable.", 404);
    if (game.download_url) {
      await db.prepare("UPDATE games SET downloads = downloads + 1 WHERE id = ?").bind(id).run();
      return new Response(null, {
        status: 302,
        headers: { Location: game.download_url, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
      });
    }
    const filename = String(game.filename || "").replace(/[\\/]/g, "");
    if (!filename || filename === "." || filename === "..") return error("Fichier de jeu invalide.", 400);
    const assetUrl = new URL(request.url);
    assetUrl.pathname = `/games/${encodeURIComponent(filename)}`;
    const upstream = await env.ASSETS.fetch(new Request(assetUrl, { method: "GET" }));
    if (!upstream.ok) return error("Fichier manquant dans les assets du site.", 404);
    await db.prepare("UPDATE games SET downloads = downloads + 1 WHERE id = ?").bind(id).run();
    const headers = new Headers(upstream.headers);
    headers.set("Content-Type", "application/octet-stream");
    headers.set("Content-Disposition", `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`);
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Cache-Control", "private, no-store");
    return new Response(upstream.body, { status: 200, headers });
  }

  if (path.startsWith("/media/covers/") && method === "GET") {
    const id = decodeURIComponent(path.slice("/media/covers/".length));
    if (!/^[\w-]{1,80}$/.test(id)) return error("Image introuvable.", 404);
    const row = await db.prepare("SELECT cover_blob, cover_type FROM games WHERE id = ?").bind(id).first();
    if (!row?.cover_blob || !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(row.cover_type)) return error("Image introuvable.", 404);
    const bytes = row.cover_blob instanceof ArrayBuffer ? new Uint8Array(row.cover_blob) : row.cover_blob;
    return new Response(bytes, { headers: { "Content-Type": row.cover_type, "Content-Length": String(bytes.byteLength), "Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff" } });
  }

  if (path === "/api/comptes" && method === "GET") {
    const auth = await requireUser(request, env, ["admin"]);
    if (auth.response) return auth.response;
    const result = await db.prepare("SELECT id, name, email, role, created_at FROM users ORDER BY created_at DESC").all();
    return json({ success: true, comptes: (result.results || []).map((row) => ({ ...safeUser(row), date: row.created_at })) });
  }

  if (path === "/api/update" && method === "POST") {
    const auth = await requireUser(request, env);
    if (auth.response) return auth.response;
    const input = await readJson(request);
    const targetId = String(input.id || auth.user.id);
    if (targetId !== auth.user.id && auth.user.role !== "admin") return error("Action non autorisée.", 403);
    const target = await db.prepare("SELECT id FROM users WHERE id = ?").bind(targetId).first();
    if (!target) return error("Compte introuvable.", 404);
    const name = String(input.name || "").trim().slice(0, 120);
    const email = String(input.email || "").trim().toLowerCase().slice(0, 240);
    const salt = String(input.password_salt || "").toLowerCase();
    const verifier = String(input.password_verifier || "").toLowerCase();
    if (input.password_verifier && (!hexBytes(salt) || salt.length !== 32 || !hexBytes(verifier) || verifier.length !== 64)) return error("Nouveau mot de passe invalide.", 400);
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return error("Adresse e-mail invalide.", 400);
    try {
      await db.prepare(
        "UPDATE users SET name = COALESCE(NULLIF(?, ''), name), email = COALESCE(NULLIF(?, ''), email), password_salt = COALESCE(NULLIF(?, ''), password_salt), password_verifier = COALESCE(NULLIF(?, ''), password_verifier) WHERE id = ?"
      ).bind(name, email, salt, verifier, targetId).run();
    } catch (cause) {
      if (String(cause?.message || "").toLowerCase().includes("unique")) return error("Email déjà utilisé.", 409);
      throw cause;
    }
    const updated = await db.prepare("SELECT id, name, email, role, created_at FROM users WHERE id = ?").bind(targetId).first();
    if (input.password_verifier) await db.prepare("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?").bind(targetId, auth.user.tokenHash).run();
    return json({ success: true, user: safeUser(updated) });
  }

  if ((path === "/api/delete-compte" || path === "/api/delete-jeu") && method === "POST") {
    const auth = await requireUser(request, env, ["admin"]);
    if (auth.response) return auth.response;
    const input = await readJson(request);
    if (path === "/api/delete-compte") {
      const id = String(input.id || "");
      if (!id || id === auth.user.id) return error("Impossible de supprimer ce compte.", 400);
      const result = await db.prepare("DELETE FROM users WHERE id = ?").bind(id).run();
      return json({ success: Number(result.meta?.changes || 0) > 0 });
    }
    const result = await db.prepare("DELETE FROM games WHERE id = ?").bind(String(input.id || "")).run();
    return json({ success: Number(result.meta?.changes || 0) > 0 });
  }

  if (path === "/api/upload-jeu" && method === "POST") {
    const auth = await requireUser(request, env, ["admin"]);
    if (auth.response) return auth.response;
    const input = await readJson(request);
    const title = String(input.titre || "").trim().slice(0, 120);
    const genre = String(input.genre || "").trim().slice(0, 80);
    const description = String(input.description || "Pas de description.").trim().slice(0, 1500);
    const fileReference = String(input.file_url || "").trim();
    if (!title || !genre) return error("Titre et genre requis.", 400);
    if (fileReference.length > 4096) return error("Le lien du fichier est trop long.", 400);
    let filename = "";
    let downloadUrl = null;
    if (fileReference.startsWith("/games/")) {
      try { filename = decodeURIComponent(fileReference.slice("/games/".length)); }
      catch { return error("Chemin de fichier invalide.", 400); }
      if (!filename || filename.includes("/") || filename.includes("\\") || filename.includes("..")) return error("Chemin de fichier invalide.", 400);
      const assetUrl = new URL(request.url);
      assetUrl.pathname = `/games/${encodeURIComponent(filename)}`;
      const asset = await env.ASSETS.fetch(new Request(assetUrl));
      if (!asset.ok) return error("Fichier absent de cloudflare/public-games.", 404);
    } else {
      let parsedUrl;
      try { parsedUrl = new URL(fileReference); }
      catch { return error("Ajoute un lien HTTPS direct ou un chemin /games/ valide.", 400); }
      if (parsedUrl.protocol !== "https:" || parsedUrl.username || parsedUrl.password) return error("Le lien du fichier doit utiliser HTTPS.", 400);
      downloadUrl = parsedUrl.toString();
      filename = parsedUrl.pathname.split("/").pop() || "jeu";
    }
    const imageType = String(input.image_type || "");
    const imageBase64 = String(input.image_base64 || "");
    let cover = null;
    if (imageBase64) {
      if (!(["image/png", "image/jpeg", "image/webp", "image/gif"].includes(imageType))) return error("Format d’image non pris en charge.", 415);
      if (imageBase64.length > Math.ceil(MAX_COVER_BYTES * 4 / 3) + 8) return error("Jaquette trop volumineuse : limite gratuite 512 Ko.", 413);
      let bytes;
      try { bytes = Uint8Array.from(atob(imageBase64), (character) => character.charCodeAt(0)); }
      catch { return error("Données de jaquette invalides.", 400); }
      if (!bytes.byteLength || bytes.byteLength > MAX_COVER_BYTES) return error("Jaquette trop volumineuse : limite gratuite 512 Ko.", 413);
      cover = bytes;
    }
    const id = crypto.randomUUID();
    const date = displayDate();
    const sizeLabel = String(input.taille || "Lien externe").trim().slice(0, 40);
    await db.prepare(
      "INSERT INTO games (id, title, genre, description, filename, download_url, size_label, size_bytes, emoji, game_date, cover_blob, cover_type, downloads, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)"
    ).bind(id, title, genre, description, filename, downloadUrl, sizeLabel, 0, String(input.emoji || "🎮").slice(0, 12), date, cover, cover ? imageType : null, Math.floor(Date.now() / 1000)).run();
    const game = await db.prepare(
      "SELECT id, title, genre, description, filename, download_url, size_label, emoji, game_date, cover_blob, downloads FROM games WHERE id = ?"
    ).bind(id).first();
    return json({ success: true, jeu: serializeGame(game) }, 201);
  }

  if (["/api/library", "/api/cloud/status", "/api/cloud/configure", "/api/cloud/sync", "/api/optimization"].includes(path)) {
    return hostedDisabled("Cette fonction est réservée à l’application desktop.");
  }

  if (path === "/api/system" && method === "GET") {
    const row = await db.prepare("SELECT COUNT(*) AS files, COALESCE(SUM(size_bytes), 0) AS data_size FROM games").first();
    return json({ success: true, system: { files: Number(row?.files || 0), data_size: Number(row?.data_size || 0), threads: "serverless", platform: "Cloudflare Workers Free" } });
  }
  return error("Route API introuvable.", 404);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/covers/")) {
        return await handleApi(request, env, ctx, url);
      }
      return env.ASSETS.fetch(request);
    } catch (cause) {
      if (cause instanceof ClientInputError) return error(cause.message, 400);
      console.error("GameVault Worker error", cause);
      return error("Erreur du service GameVault. Réessaie plus tard.", 500);
    }
  },
};
