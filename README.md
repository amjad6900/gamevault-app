# GameVault

GameVault est une bibliothèque de jeux locale avec une interface de boutique, une bibliothèque installée, un lancement de jeux, une synchronisation WebDAV et une application Electron pour Windows.

## Démarrage local

Prérequis : Node.js 20+ et Python 3.11+.

```powershell
npm install
npm start
```

Le serveur se lie à `127.0.0.1`. Les comptes, téléchargements, jaquettes et paramètres WebDAV sont conservés localement. Changez le mot de passe administrateur initial dès la première connexion.

## Publication

Le site GitHub Pages fournit une page de présentation et de téléchargement. Les fonctions qui utilisent l’API (comptes, boutique interactive, bibliothèque et WebDAV) nécessitent le serveur Python local ; elles ne peuvent pas fonctionner sur un hébergement statique seul.

À chaque tag `v*`, GitHub Actions construit l’installateur NSIS et l’application portable Windows, puis crée une release. Les dossiers de données locales, les comptes, les secrets WebDAV et les jeux personnels sont exclus du dépôt.

## Sécurité et données

Ne publiez pas `repertoire/comptes.txt`, `games/` ni `cloud.json`. Ils sont ignorés par Git et peuvent contenir des données ou des mots de passe personnels.
