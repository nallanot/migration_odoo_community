#!/usr/bin/env python3
import os
import subprocess
import time
import json

def run_migration():
    # --- Chargement du manifest ou valeur par défaut ---
    manifest_path = "/data/manifest.json"
    if os.path.exists(manifest_path):
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
            raw_name = manifest.get("db_name", "odoo_migrated")
            print("📄 Manifest détecté :")
            print(json.dumps(manifest, indent=2))
    else:
        raw_name = "odoo_migrated"
    # Normalisation du nom (minuscules, underscores)
    db_name = raw_name.lower().replace(" ", "_")

    dump_path = "/data/dumps/dump.sql"
    db_user = os.environ.get("POSTGRES_USER", "odoo")          # superutilisateur et user Odoo
    admin_user = db_user
    admin_pass = os.environ.get("POSTGRES_PASSWORD", "odoo")
    db_host = os.environ.get("DB_HOST", "db")
    filestore_path = "/data/filestore"

    print(f"\n🔄 Base cible : {db_name} | Version initiale : 12.0")
    print("📈 Plan de migration : 12.0 ➜ 13.0 ➜ 14.0 ➜ 15.0 ➜ 16.0 ➜ 17.0 ➜ 18.0")

    # Vérification du filestore
    if not os.path.isdir(filestore_path) or not os.listdir(filestore_path):
        print(f"⚠️ Aucun filestore valide détecté dans {filestore_path}")
    else:
        print("✅ Filestore détecté.")

    # Mise en place du mot de passe
    os.environ['PGPASSWORD'] = admin_pass

    # --- DROP DATABASE ---
    print("🗄️ Suppression de l'ancienne base (si existante)...")
    subprocess.run([
        'psql', '-h', db_host, '-U', admin_user,
        '-c', f"DROP DATABASE IF EXISTS {db_name};"
    ], check=True)

    # --- CREATE DATABASE & ROLE ---
    print("🗄️ Création du rôle Odoo et de la base...")
    # Création du rôle si nécessaire
    subprocess.run([
        'psql', '-h', db_host, '-U', admin_user, '-d', 'postgres',
        '-c', (
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{db_user}') THEN "
            "CREATE ROLE {db_user} LOGIN PASSWORD '{admin_pass}'; END IF; "
            "END $$;"
        ).format(db_user=db_user, admin_pass=admin_pass)
    ], check=True)
    # Création de la base
    subprocess.run([
        'psql', '-h', db_host, '-U', admin_user,
        '-c', f"CREATE DATABASE {db_name} OWNER {db_user};"
    ], check=True)

    # --- Attente de la dispo de la DB ---
    print("⏳ Attente de la disponibilité de la base...")
    for i in range(20):
        r = subprocess.run([
            'psql', '-h', db_host, '-U', admin_user,
            '-d', db_name, '-c', 'SELECT 1;'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            print("✅ Base prête.")
            break
        time.sleep(2)
    else:
        print("❌ La base n'est pas accessible après création.")
        return

    # --- RESTAURATION DU DUMP ---
    print(f"🗄️ Restauration du dump : {dump_path}")
    with open(dump_path, 'r') as f:
        data = f.read().replace('OWNER TO', '-- OWNER TO')
    with open(dump_path, 'w') as f:
        f.write(data)
    subprocess.run([
        'psql', '-h', db_host, '-U', db_user,
        '-d', db_name, '-v', 'ON_ERROR_STOP=1', '-f', dump_path
    ], check=True)
    print("✅ Dump restauré avec succès.")

    # --- MIGRATIONS OPENUPGRADE ---
    versions = ["13.0","14.0","15.0","16.0","17.0","18.0"]
    for ver in versions:
        run_openupgrade(ver, db_name)


def run_openupgrade(version, db_name):
    print(f"🚀 Migration vers Odoo {version}")
    odoo_path = f"/app/openupgrade/{version}"
    env = os.environ.copy()
    subprocess.run([
        'python3', f'{odoo_path}/odoo-bin',
        '--config=/etc/odoo/odoo.conf',
        '-d', db_name,
        '--update=all',
        '--stop-after-init'
    ], cwd=odoo_path, env=env, check=True)
    print(f"✅ Migration vers {version} terminée.")


if __name__ == '__main__':
    run_migration()
