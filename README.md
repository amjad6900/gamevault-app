# GameVault 4.1

GameVault est devenu une application Windows 11 installable, avec un mode local rapide, une bibliothèque de jeux, une synchronisation cloud WebDAV et une maintenance intégrée.

## Lancer en développement

Prérequis : Python 3.11+ et Node.js 20+.

```powershell
npm install
npm start
```

En développement local, le compte administrateur de démonstration est `admin@gamevault.com` / `admin123`. Changez immédiatement ce mot de passe depuis **Mon profil**. Ces identifiants sont désactivés en mode hébergé.

## Construire l’installateur Windows

Depuis PowerShell sur Windows 11 :

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows.ps1
```

Le script compile d’abord le serveur Python avec PyInstaller, puis crée un installateur NSIS et une version portable dans `dist-app/`. L’application lance automatiquement son serveur local et ouvre l’interface comme une application native. Les données modifiables sont stockées dans `%APPDATA%\GameVault\data`.

## Cloud

Dans **Cloud & optimisation**, renseignez un endpoint WebDAV et un identifiant/token. Nextcloud, ownCloud et la plupart des NAS proposent une URL WebDAV. Les modes disponibles sont :

- **Sauvegarder** : envoie les données locales vers le cloud ;
- **Restaurer** : récupère les données cloud ;
- **Synchroniser** : rapproche les deux côtés et signale les conflits.

Avant une restauration ou une synchronisation bidirectionnelle, GameVault crée automatiquement une sauvegarde locale rotative (5 dernières archives) dans `%APPDATA%\GameVault\data\backups`.

Le mot de passe est conservé dans le profil local avec des droits restreints. Pour un environnement sensible, utilisez un token WebDAV dédié et révoquez-le quand nécessaire.

## Bibliothèque locale

Dans l’application desktop, les téléchargements sont écrits progressivement dans `%APPDATA%\GameVault\library` (au lieu d’être conservés entièrement en mémoire). L’onglet **Ma bibliothèque** permet d’ouvrir le dossier ou de lancer un fichier téléchargé avec l’application Windows associée.

## Performance et sécurité

- serveur HTTP multithread et téléchargements en morceaux avec reprise ;
- suppression contrôlée des fichiers temporaires `.part` ;
- mots de passe PBKDF2-SHA256, avec migration automatique des anciens comptes ;
- données utilisateur séparées des ressources de l’application ;
- fenêtre Electron isolée (context isolation, pas de Node.js dans les pages).
- serveur lié à `127.0.0.1` par défaut pour ne pas exposer les actions d’administration au réseau local.

Les imports sont écrits progressivement sur disque. Par défaut, GameVault n’impose pas de limite de taille aux jeux (`GAMEVAULT_MAX_UPLOAD_MB` permet d’en définir une).

## Plugin Roblox Studio

Le dossier [roblox-plugin](roblox-plugin/) contient un plugin Studio avec panneau GameVault. Il communique avec l’application desktop via `HttpService` sur `127.0.0.1:37421`, sans embarquer de clé Roblox.

Roblox autorise officiellement les plugins Studio à utiliser `HttpService` pour communiquer avec `localhost`/`127.0.0.1`, après accord de permission de l’utilisateur. La publication automatisée d’une place doit rester côté GameVault et utiliser Open Cloud avec une clé limitée à l’univers concerné.

## Site web gratuit

La vitrine GitHub Pages publie la page de téléchargement. L’application web complète (comptes, boutique, statistiques et jaquettes) utilise Cloudflare Workers et D1; le serveur Python et WebDAV restent dans l’application desktop. Le Worker répond à la demande, sans maintenir un processus Python actif en permanence.

Le plan gratuit inclut jusqu’à 100 000 requêtes Worker par jour, 5 Go de D1 et des assets statiques d’au plus 25 Mio par fichier (20 000 fichiers par version). Si une limite est atteinte, l’API ou les écritures peuvent être suspendues jusqu’au prochain renouvellement de quota. Consultez les [limites Workers](https://developers.cloudflare.com/workers/platform/limits/) et [tarifs D1](https://developers.cloudflare.com/d1/platform/pricing/).

Depuis la racine du projet, configurez le service gratuit :

```powershell
npm run cloudflare:login
npm run cloudflare:database
npm run cloudflare:migrate
npm run cloudflare:admin
npm run cloudflare:deploy
```

Choisissez le plan **Workers Free** dans Cloudflare. La commande administrateur demande une adresse et un mot de passe unique de 16 caractères minimum dans le terminal; le mot de passe brut n’est pas transmis au Worker ni ajouté au dépôt. La dernière commande affiche l’adresse publique `workers.dev`.

Les jaquettes sont stockées dans D1 et limitées à 512 Ko chacune, dans la limite globale gratuite de la base. Pour ajouter un jeu, l’administrateur peut donner un lien HTTPS direct ou placer un fichier redistribuable de 25 Mio maximum dans `cloudflare/public-games`, puis saisir `/games/nom-du-fichier.zip`. Les jeux et comptes présents dans les dossiers locaux `games/` et `repertoire/` ne sont jamais copiés automatiquement dans le site public. Au premier déploiement, ajoutez les jeux voulus depuis le compte propriétaire.

Les fichiers ajoutés à `cloudflare/public-games` sont publiés dans un dépôt public. N’y placez que des contenus que vous pouvez distribuer. Pour les archives supérieures à 25 Mio, utilisez un hébergeur HTTPS externe; aucun stockage R2 payant n’est configuré.

Le workflow `.github/workflows/pages.yml` publie `download.html` sur GitHub Pages. Le workflow `.github/workflows/build-windows.yml` compile les packages NSIS et portable sur Windows à la création d’une release `v*`.

## Licence

GameVault est distribué sous licence propriétaire « All rights reserved », au nom du compte GitHub `amjad6900`. Consultez [LICENSE](LICENSE). Le dépôt public ne concède pas de permission de réutilisation.

Ne publiez jamais vos données locales. Le fichier `repertoire/comptes.txt` peut contenir d’anciens mots de passe en clair ; les dossiers `repertoire/` et `games/`, ainsi que `cloud.json`, sont exclus par `.gitignore`.
