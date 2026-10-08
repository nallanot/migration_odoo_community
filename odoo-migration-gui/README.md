# Assistant graphique de migration Odoo Community 12 → 19

Assistant à lancer **sur ton serveur Ubuntu hébergeant Docker**. Interface dans
le navigateur, Python sans dépendance externe. Il prépare et exécute les étapes
OpenUpgrade 12→13→14→15→16→17→18→19, avec un contrôle humain entre chaque étape.
Ce n'est pas une garantie de compatibilité des modules tiers : les extensions
comptables et autres modules doivent disposer de code et scripts adaptés à chaque version.

## Démarrer

1. Copier et extraire `odoo-migration-gui.zip` sur le serveur Ubuntu.
2. Dans le dossier extrait :

```bash
chmod +x lancer.sh
./lancer.sh
```

Prérequis : Python **3.9 ou supérieur**, Docker accessible à ton compte, Git,
Internet pour GitHub/PyPI/Docker Hub. Aucune installation pip sur l'hôte.
Les images anciennes peuvent demander des correctifs de dépendances Python.
Prévoir au minimum 20 Gio libres, davantage si le filestore est volumineux ;
plusieurs copies et images sont conservées. `./lancer.sh --workdir /chemin/stockage/odoo-migration`
permet de choisir un disque local adapté. Ne pas utiliser le répertoire de données
PostgreSQL de production.

Le terminal affiche un lien `http://127.0.0.1:8765/#…` contenant un jeton privé.
Garder ce terminal ouvert pendant les opérations. Relancer avec le même dossier
de travail reprend les points validés et les essais en attente.

Depuis un autre ordinateur, ouvrir un deuxième terminal :

```bash
ssh -L 8765:127.0.0.1:8765 -L 18069:127.0.0.1:18069 ubuntu@IP_DU_SERVEUR
```

Puis ouvrir **le lien complet affiché par l'assistant** sur cet ordinateur.
Le tunnel doit rester ouvert. Depuis un iPad, utiliser un client SSH prenant
en charge les tunnels locaux. Les ports 8765 et 18069 sont limités à localhost ;
ne pas placer l'assistant derrière Traefik ou l'exposer sur Internet.

## Parcours adapté à ton installation

- Conteneur Odoo : `odoo12`.
- Conteneur SQL : `odoo12-db` (`postgres:10`).
- Base à migrer : `NicolasAllanot`.
- Base secondaire `AllanotNicolas` : sauvegardée avec les autres bases non système,
  sans être traitée comme une base Odoo initialisée.

1. **Diagnostic** : image, version de base, modules actifs, chemins et espace libre.
2. **Sauvegarde** : autoriser une interruption de quelques minutes, variable
   selon la taille du conteneur et du filestore. Odoo est arrêté ; toutes les bases
   métier sont dumpées ; les volumes applicatifs et chemins d'addons configurés
   sont copiés. L'export du système de fichiers préserve les modifications et
   dépendances hors volumes. La configuration Docker complète est enregistrée
   dans un fichier privé. Odoo est redémarré dans un bloc `finally`, même en cas
   d'erreur de sauvegarde (si Docker reste disponible).
3. **Préparer** : construire l'image de la version suivante. Les commits téléchargés
   sont figés dans le dossier de travail ; les relances réutilisent ces sources.
4. **Revoir les modules** : suivre le lien de couverture OCA et analyser les modules
   absents ou tiers. La simple présence d'un module ne prouve pas sa compatibilité.
5. **Migrer** : chaque essai restaure le dernier dump validé sur une nouvelle base,
   compare les compteurs avant migration et duplique le filestore.
6. **Contrôler** : démarrer la copie de test et examiner factures, paiements,
   rapports, contacts, ventes, employés et pièces jointes avec les comptes existants.
7. **Valider** : noter les contrôles et expliquer les écarts. Le serveur vérifie
   l'existence des fichiers référencés par les pièces jointes et capture un nouveau
   dump + filestore. La copie web est arrêtée avant cette capture.
8. Reprendre à « Préparer » pour l'étape suivante.

La première restauration prouve que le dump principal est exploitable et que
les compteurs/totaux sélectionnés concordent. Les dumps secondaires sont conservés,
mais leur restauration n'est pas testée automatiquement. Les comptes et totaux
globaux ne remplacent pas les validations métier par société, devise et période.
Les données de facturation changent notamment de modèle entre 12 et 13.

## Modules tiers et modules retirés

Tu as notamment `om_account_accountant`, `om_account_asset`, `om_account_budget`,
`accounting_pdf_reports`, `backend_theme_v12`, `sinerkia_jitsi_meet`,
`prt_report_attachment_preview`, `quick_language_selection` et `web_responsive`.
**Il est probable que le premier essai soit bloqué tant que leur traitement
n'est pas préparé.** Le programme ne les désinstalle pas et ne les efface pas du SQL.

Mettre les modules adaptés dans :

```text
work/extra/13/nom_module/__manifest__.py
work/extra/14/depot_oca/nom_module/__manifest__.py
```

Puis reconstruire l'image avec le bouton « Préparer ». Ne pas copier aveuglément
les modules 12 dans 13 : migrer leur code, les données et les dépendances externes.
Le Dockerfile est généré dans `work/build/<version>/Dockerfile`. Il peut être
ajusté pour ajouter des bibliothèques, puis construit manuellement avec
`docker build -t odoo-migration-local:13 work/build/13`. Pour faire enregistrer
l'image modifiée dans l'assistant sans perdre les adaptations, copier le Dockerfile
adapté vers `work/build/13/Dockerfile.custom`, puis cliquer sur « Préparer ».
L'assistant réutilise ce fichier personnalisé et enregistre l'image obtenue.

Pour un module absent dont OpenUpgrade traite réellement la suppression/fusion :

```json
{
  "web_settings_dashboard": {
    "action": "openupgrade",
    "evidence": "Couverture OCA 12→13 : fusion dans base_setup ; scripts vérifiés."
  }
}
```

Cette déclaration ne crée aucun script et n'effectue aucune suppression.
Elle consigne ta vérification et lève le blocage de présence. Une affirmation
inexacte peut laisser des données non migrées : seul le code OpenUpgrade/tiers
réellement présent effectue le travail. À partir de 14, les actions `rename` et
`merge` permettent de transmettre des mappings privés aux mécanismes OpenUpgrade,
avec un module `target` présent et une `evidence` documentée. Elles demandent une
analyse et, généralement, des scripts métier ; elles ne constituent pas une
solution automatique aux modules comptables manquants.

Les renommages/fusions détectés dans `apriori.py` sont affichés comme aide.
Les scripts OCA peuvent aussi traiter des retraits ailleurs ; l'assistant ne
déduit pas une couverture complète de la présence de ce fichier. Même quand
aucun module n'est absent, la couverture officielle doit être vérifiée.

## Docker et isolation

- Pas d'accès au réseau Traefik ou aux volumes de production pour les essais.
- PostgreSQL isolé : version 14 pour cibles 13–15, version 16 pour 16–19.
  Chaque passage restaure un dump logique ; les fichiers PostgreSQL 10 ne sont
  jamais montés sur une version récente.
- OpenUpgrade 13 : fork complet, exécuté avec `odoo-bin`.
- OpenUpgrade 14–19 : image Odoo officielle, framework et scripts OCA,
  `--load=base,web,openupgrade_framework`, `--upgrade-path`, `--update all`.
- Réseaux internes Docker sans sortie réseau pour la copie ; tâches planifiées,
  serveurs de courriels et collecte des courriels désactivés sur la copie.
  Les intégrations et OAuth nécessitant Internet ne fonctionneront pas dans ce test.
- Identifiants PostgreSQL aléatoires et configuration privée ; serveur Odoo
  exécuté avec l'utilisateur `odoo`.
- L'assistant possède les droits de ton compte Docker : réserver son jeton
  et son dossier de travail aux administrateurs de ce serveur.

## Reprise, journaux et sauvegardes

- `work/backups/` : capture initiale, dumps, filestore, addons et manifeste SHA-256.
  Copier cette sauvegarde sur un stockage indépendant avant de poursuivre.
- `work/checkpoints/` : dumps et filestores validés version par version.
- `work/runs/` : essais, configurations privées et journal complet OpenUpgrade.
- `work/assistant.log` : journal de l'assistant.
- `work/build/` : sources Git figées, Dockerfiles, inventaires et commits.

Une relance après un échec technique utilise le checkpoint précédent, jamais
une base partiellement migrée. Pour un essai techniquement terminé mais incorrect,
« Rejeter cet essai » permet de recommencer depuis ce checkpoint.
Après une interruption de l'assistant, vérifier Docker : le processus de migration
peut continuer. L'assistant bloque les nouveaux essais tant qu'un conteneur
`*-upgrade` étiqueté `odoo.migration=local` tourne encore. Un essai terminé pendant
une interruption peut être relancé depuis le dernier checkpoint ; il n'est pas
promu automatiquement. Ne pas interrompre le serveur pendant la capture initiale.

Les ressources des essais ne sont **pas supprimées automatiquement**. Pour les
identifier sans toucher à la production :

```bash
docker ps -a --filter label=odoo.migration=local
docker volume ls --filter label=odoo.migration=local
docker network ls --filter label=odoo.migration=local
```

Ne supprimer que les ressources identifiées après sauvegarde et clôture des tests.
Après la validation 19, préparer une nouvelle capture récente pour la répétition
finale et planifier le basculement (Traefik, domaine, accès, courriels et sauvegardes).
Cet assistant **ne remplace pas la production** et ne réactive pas les intégrations.

## Vérifications du paquet

```bash
python3 -m unittest -v test_migration.py
```

Les tests vérifient l'isolation des commandes, les blocages, la restauration,
la reprise et l'authentification HTTP. Aucune migration de ta base n'a été
effectuée lors de la création de ce paquet ; un essai réel sur ton serveur reste requis.

Sources : [OCA – exécution des migrations](https://oca.github.io/OpenUpgrade/040_run_migration.html),
[couverture par version](https://oca.github.io/OpenUpgrade/),
[OpenUpgrade](https://github.com/OCA/OpenUpgrade).
