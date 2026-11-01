# GameVault Studio plugin

Ce plugin ajoute un panneau **GameVault** dans Roblox Studio. Il se connecte au pont local de l’application via `HttpService` et permet de vérifier la connexion, voir les projets `.rbxl/.rbxlx` publiés dans GameVault et ouvrir la boutique.

## Installation locale

1. Lancez GameVault Desktop.
2. Dans Roblox Studio, ouvrez `Plugins > Manage Plugins > Open Plugins Folder`.
3. Copiez `GameVaultPlugin.lua` dans ce dossier, puis redémarrez Studio.
4. Cliquez sur **GameVault** dans l’onglet Plugins.
5. Acceptez la permission HTTP demandée pour `127.0.0.1`.

Le port par défaut est `37421` pour l’application Electron. Si vous lancez seulement `serveur_local.py`, utilisez le port configuré dans le plugin (souvent `8080`).

## Sécurité

Le plugin ne contient aucune clé Roblox Open Cloud. Il ne communique qu’avec GameVault sur `127.0.0.1`. Les clés de publication doivent rester dans l’application desktop ou dans un service backend protégé.

La publication directe d’une place Roblox utilise ensuite l’API Open Cloud côté GameVault, avec `Universe ID`, `Place ID` et une clé ayant uniquement la permission nécessaire.
