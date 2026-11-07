import { randomBytes, randomUUID, pbkdf2 } from "node:crypto";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { promisify } from "node:util";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const derive = promisify(pbkdf2);

async function promptLine(label) {
  const prompt = createInterface({ input: stdin, output: stdout });
  try { return await prompt.question(label); }
  finally { prompt.close(); }
}

async function promptHidden(label) {
  if (!stdin.isTTY || typeof stdin.setRawMode !== "function") return promptLine(`${label} (saisie masquée indisponible) : `);
  return new Promise((resolve, reject) => {
    let value = "";
    const previousRawMode = Boolean(stdin.isRaw);
    const finish = (error) => {
      stdin.removeListener("data", onData);
      stdin.setRawMode(previousRawMode);
      stdin.pause();
      stdout.write("\n");
      if (error) reject(error); else resolve(value);
    };
    const onData = (chunk) => {
      for (const character of String(chunk)) {
        if (character === "\u0003") return finish(new Error("Configuration interrompue."));
        if (character === "\r" || character === "\n") return finish();
        if (character === "\u007f" || character === "\b") value = value.slice(0, -1);
        else if (character >= " ") value += character;
      }
    };
    stdout.write(label);
    stdin.setRawMode(true);
    stdin.resume();
    stdin.on("data", onData);
  });
}

function sql(value) { return `'${String(value).replaceAll("'", "''")}'`; }

const email = (await promptLine("Adresse e-mail propriétaire : ")).trim().toLowerCase();
if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error("Adresse e-mail invalide.");
const password = await promptHidden("Mot de passe propriétaire (16 caractères minimum) : ");
const confirmation = await promptHidden("Confirme le mot de passe : ");
if (password.length < 16) throw new Error("Le mot de passe doit contenir au moins 16 caractères.");
if (password !== confirmation) throw new Error("Les deux mots de passe ne correspondent pas.");

const salt = randomBytes(16);
const verifier = await derive(password, salt, 210000, 32, "sha256");
const id = `owner-${randomUUID()}`;
const createdAt = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris" }).format(new Date());
const statement = `INSERT INTO users (id, name, email, password_salt, password_verifier, role, created_at) VALUES (${sql(id)}, 'Propriétaire', ${sql(email)}, ${sql(salt.toString("hex"))}, ${sql(verifier.toString("hex"))}, 'admin', ${sql(createdAt)}) ON CONFLICT(email) DO UPDATE SET name = 'Propriétaire', password_salt = excluded.password_salt, password_verifier = excluded.password_verifier, role = 'admin';\n`;

const tempDirectory = path.join(root, ".tmp");
const tempFile = path.join(tempDirectory, `admin-${randomUUID()}.sql`);
await mkdir(tempDirectory, { recursive: true });
await writeFile(tempFile, statement, { encoding: "utf8", mode: 0o600 });
try {
  const command = process.platform === "win32" ? "npx.cmd" : "npx";
  const result = spawnSync(command, ["--yes", "wrangler@latest", "d1", "execute", "gamevault-db", "--remote", "--file", tempFile], {
    cwd: root,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error("Échec de création du compte propriétaire dans D1.");
  console.log("Compte administrateur propriétaire enregistré dans D1. Le mot de passe brut n’a pas été envoyé au serveur.");
} finally {
  await rm(tempFile, { force: true });
  try { await rm(tempDirectory, { recursive: false }); } catch {}
}
