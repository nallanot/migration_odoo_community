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
