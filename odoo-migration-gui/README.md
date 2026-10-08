# Assistant graphique de migration Odoo Community

Assistant local pour migrer une base Odoo Community à travers les versions majeures avec OpenUpgrade. La migration s'effectue sur des copies Docker indépendantes. L'interface permet le diagnostic, la sauvegarde, l'exécution, l'examen des écarts et la reprise d'une étape.

## Déployer avec Docker Compose

Depuis la racine du dépôt :

```bash
docker compose -f docker-compose.assistant.yml up -d --build
docker compose -f docker-compose.assistant.yml logs assistant
```

Dans un gestionnaire de stacks Git, sélectionner explicitement `docker-compose.assistant.yml`. Le dépôt étant privé, le gestionnaire doit disposer d'un accès Git valide.

La stack conserve son travail dans `/opt/odoo-migration` sur l'hôte. Pour choisir un autre disque, définir `ODOO_MIGRATION_HOME` avec un chemin absolu local avant de déployer. Ce dossier contient des sauvegardes et des secrets : réserver son accès aux administrateurs et le sauvegarder sur un autre support. L'assistant utilise le socket Docker pour créer ses conteneurs de travail ; les personnes ayant accès à son interface ou à son jeton disposent indirectement de privilèges Docker.

L'interface écoute sur `127.0.0.1:8765` de l'hôte. Le journal du conteneur affiche son lien avec jeton. Depuis un ordinateur distant, ouvrir un tunnel SSH vers les ports de l'interface et de la copie de test :

```bash
ssh -L 8765:127.0.0.1:8765 -L 18069:127.0.0.1:18069 utilisateur@serveur
```

Ouvrir ensuite le lien complet du journal dans le navigateur du poste possédant le tunnel. Garder le tunnel ouvert pendant l'utilisation.

## Plugin ChatGPT avec un dump

Le [plugin privé Agent migration Odoo](https://migration-odoo-agent.n-allanot.chatgpt.site) peut créer un travail, recevoir une sauvegarde et suivre la migration depuis ChatGPT. Le ZIP de sauvegarde Odoo doit contenir `dump.sql` et, si des pièces jointes existent, `filestore/`. Un dump PostgreSQL au format personnalisé (`pg_dump -Fc`) est aussi accepté, mais ne contient pas le filestore. La taille maximale est de 10 Gio.

Pour relier ce plugin à la stack Docker, définir les variables suivantes dans les secrets ou l'environnement du gestionnaire de stack, puis redéployer `docker-compose.assistant.yml` :

```text
MIGRATION_PLUGIN_SITE_URL=https://migration-odoo-agent.n-allanot.chatgpt.site
MIGRATION_PLUGIN_TOKEN=<code généré par l'outil create_worker_pairing_code du plugin>
MIGRATION_PLUGIN_SERVICE_TOKEN=<jeton d'accès de service du Site privé>
```

Ne placer aucun de ces jetons dans Git. Le conteneur interroge le plugin par HTTPS ; aucun port entrant public n'est nécessaire. Dans ChatGPT, créer un travail avec `create_migration`, ouvrir son lien d'envoi, fournir le dump, puis le mettre en file avec `queue_migration`. `get_migration` affiche le journal, le stade et les blocages. Un fichier de module préparé dans le chat peut être ajouté avec `add_migration_file` ; relancer ensuite le travail avec `queue_migration`. `delete_migration` supprime le dump et les fichiers hébergés du plugin. Les copies locales se trouvent dans `<dossier_de_travail>/jobs/<id>/` et se nettoient séparément après vérification.

Le moteur importe la sauvegarde sur une copie PostgreSQL isolée et sauvegarde un premier point de reprise avant les migrations. Il s'arrête aux erreurs et aux modules sans adaptation fiable. La mise en production et la validation métier restent à organiser après examen de la copie finale.

## Lancement automatique

Saisir les noms des deux conteneurs et de la base dans le diagnostic, puis cocher l'autorisation de sauvegarde. Le bouton « Tout lancer automatiquement » enchaîne les étapes de la version 12 jusqu'à la version 19. La capture initiale arrête temporairement l'application source pour garder la base et les fichiers synchronisés, puis la redémarre. Les migrations suivantes utilisent seulement des copies isolées.

L'assistant s'arrête lorsque le code d'un module manque, qu'un module tiers n'a pas de scripts de migration détectables, qu'une commande échoue, ou qu'un compteur ou total diverge. Il laisse intact le dernier point validé. Ajouter le code adapté dans `<dossier_de_travail>/extra/<version>/`, examiner les écarts, puis relancer. Une présence de script ne garantit pas la correction fonctionnelle : vérifier les factures, les paiements, les droits d'accès, les flux métier et les pièces jointes avant tout basculement.

Les versions intermédiaires enregistrent leurs dumps et filestores dans `checkpoints/`. Les essais et leurs journaux sont dans `runs/`. Les codes OpenUpgrade figés et les images construites sont dans `build/`. Les ressources de test portent l'étiquette Docker `odoo.migration=local`. La copie finale est servie sur le port de test 18069. La mise en production est une opération distincte à planifier après les contrôles métier.

L'automatisation ne désinstalle pas les modules manquants ni ne transforme une fusion métier supposée en migration SQL. Les renommages, fusions ou modules tiers nécessitant des adaptations peuvent être traités avec les contrôles manuels de l'interface. Cette limite évite de valider automatiquement une perte de données.

## Mode local sans conteneur

Python 3.9+, Git et Docker sont nécessaires sur l'hôte. Dans ce dossier :

```bash
./lancer.sh --workdir /chemin/stockage/migration
```

Le lien local est affiché dans le terminal. L'option `--bind` permet au conteneur de servir l'interface ; l'exposition réseau de l'hôte reste limitée par Compose.

## Tests

```bash
python3 -m unittest -v test_migration.py
```

Les tests couvrent les blocages, la copie de sauvegarde, les points de reprise, la vérification des pièces jointes et l'authentification HTTP. Une migration réelle sur les données source n'est pas simulée par ces tests. Consulter la [documentation d'exécution OpenUpgrade](https://oca.github.io/OpenUpgrade/040_run_migration.html) et la [couverture par version](https://oca.github.io/OpenUpgrade/).
