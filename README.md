# Odoo CE 12 ➜ 18 Migration Tool

Ce projet fournit un environnement Docker minimal permettant de migrer une base
Odoo Community Edition 12 vers la version 18 à l'aide d'OpenUpgrade.

## Utilisation

1. Lancez `docker-compose up --build`.
2. Ouvrez `http://localhost:8080` puis déposez une archive `.zip` contenant :
   - `dump.sql` : export de votre base v12.
   - `filestore/` : (facultatif) dossier filestore sans sous-répertoires.
   - `manifest.json` : (facultatif) paramètres et séquence de migration.
3. Une fois le fichier envoyé, la migration se lance automatiquement. Consultez
   les logs du conteneur `web` pour suivre l'avancement.

### Obtention d'OpenUpgrade

Clonez les dépôts OpenUpgrade pour chaque version souhaitée (13.0 à 18.0) dans
le répertoire `/app/openupgrade` (modifiable via `openupgrade_base`):

```bash
mkdir -p /app/openupgrade
cd /app/openupgrade
git clone -b 13.0 https://github.com/OCA/OpenUpgrade.git openupgrade_13
git clone -b 14.0 https://github.com/OCA/OpenUpgrade.git openupgrade_14
git clone -b 15.0 https://github.com/OCA/OpenUpgrade.git openupgrade_15
git clone -b 16.0 https://github.com/OCA/OpenUpgrade.git openupgrade_16
git clone -b 17.0 https://github.com/OCA/OpenUpgrade.git openupgrade_17
git clone -b 18.0 https://github.com/OCA/OpenUpgrade.git openupgrade_18
```

### Exemple de manifest

```json
{
  "db_name": "ma_base",
  "db_user": "odoo",
  "db_password": "odoo",
  "db_host": "db",
  "versions": ["13.0", "14.0", "15.0", "16.0", "17.0", "18.0"],
  "openupgrade_base": "/app/openupgrade"
}
```

Chaque clé est optionnelle et possède une valeur par défaut. La liste
`versions` définit la séquence de mises à jour OpenUpgrade appliquées.


## Assistant graphique pour Odoo 12 → 19

L'assistant local guidé est disponible dans [odoo-migration-gui](odoo-migration-gui/README.md). Il sauvegarde l'instance Docker, prépare les étapes OpenUpgrade sur des copies isolées et demande une validation de chaque version. Démarrage sur le serveur : `cd odoo-migration-gui && ./lancer.sh`. Le parcours historique ci-dessus reste disponible séparément.
