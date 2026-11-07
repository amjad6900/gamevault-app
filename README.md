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

## Site web et publication

GameVault utilise un serveur Python pour les comptes, la boutique et les téléchargements. GitHub Pages héberge le site statique de présentation et de téléchargement; le Blueprint Render décrit le service web complet et son stockage persistant.

Le workflow `.github/workflows/pages.yml` publie `download.html` sur GitHub Pages à chaque modification. Le workflow `.github/workflows/build-windows.yml` compile les packages NSIS et portable sur Windows à la création d’une release `v*`.

## Serveur public 24 h/24

Le service public est préparé pour Render avec `render.yaml` et `Dockerfile`. Il héberge la boutique web et son API Python sur une instance payante qui ne s’endort pas, avec un disque persistant de 10 Go monté sur `/var/data`. Les comptes, jaquettes, jeux et statistiques restent sur ce disque après un redémarrage ou un nouveau déploiement.

Pour le créer, connectez le dépôt `amjad6900/gamevault-app` à Render comme **Blueprint**. Render demandera `GAMEVAULT_ADMIN_EMAIL` et `GAMEVAULT_ADMIN_PASSWORD` : choisissez l’adresse propriétaire et un mot de passe unique d’au moins 16 caractères directement dans Render. Ne mettez jamais ces secrets dans GitHub. L’administrateur est créé ou renforcé au premier démarrage. Les jeux se téléchargent depuis le site; l’installation, le lancement local et WebDAV restent dans l’application desktop.

L’instance configurée est le plan Render `starter` (7 $ US/mois) et son disque de 10 Go (2,50 $ US/mois), soit environ 9,50 $ US/mois avant les dépassements de bande passante et de stockage. Le service payant reste actif en continu, mais une maintenance du fournisseur peut occasionner une courte interruption. Le disque peut être agrandi après sa création; Render ne permet pas de le réduire.

Le profil hébergé bind le serveur sur l’interface publique derrière HTTPS Render, utilise un compte propriétaire configuré par secrets et n’expose pas l’exécution de jeux ni les paramètres WebDAV partagés. Le mode desktop reste lié à `127.0.0.1`.

## Licence

GameVault est distribué sous licence propriétaire « All rights reserved », au nom du compte GitHub `amjad6900`. Consultez [LICENSE](LICENSE). Le dépôt public ne concède pas de permission de réutilisation.

Ne publiez jamais vos données locales. Le fichier `repertoire/comptes.txt` peut contenir d’anciens mots de passe en clair ; les dossiers `repertoire/` et `games/`, ainsi que `cloud.json`, sont exclus par `.gitignore`.
