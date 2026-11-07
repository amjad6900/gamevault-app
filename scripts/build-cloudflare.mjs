import { cp, mkdir, readdir, rm, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, ".cloudflare-public");
const repository = process.env.GAMEVAULT_REPOSITORY || "amjad6900/gamevault-app";

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });

const siteFiles = ["index.html", "store.html", "accounts.html", "download.html"];
let assetCount = siteFiles.length;
for (const name of siteFiles) {
  let content = await readFile(path.join(root, name), "utf8");
  content = content.replaceAll("OWNER/REPOSITORY", repository);
  await writeFile(path.join(output, name), content, "utf8");
}

const publicGames = path.join(root, "cloudflare", "public-games");
await mkdir(path.join(output, "games"), { recursive: true });
try {
  const entries = await readdir(publicGames, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile() || entry.name.startsWith(".") || entry.name.toLowerCase() === "readme.md") continue;
    const size = (await stat(path.join(publicGames, entry.name))).size;
    if (size > 25 * 1024 * 1024) throw new Error(`${entry.name} dépasse la limite Cloudflare gratuite de 25 Mio par fichier.`);
    assetCount += 1;
    if (assetCount > 20_000) throw new Error("Cloudflare limite une version Worker à 20 000 fichiers statiques.");
    await cp(path.join(publicGames, entry.name), path.join(output, "games", entry.name));
  }
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
}
console.log(`${assetCount} assets GameVault préparés dans ${output}`);
