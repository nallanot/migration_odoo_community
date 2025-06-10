from flask import Flask, request
import os, zipfile, shutil, subprocess

app = Flask(__name__)
UPLOAD_DIR = "/data/upload"
DEST_DUMPS = "/data/dumps"
DEST_FILESTORE = "/data/filestore"
DEST_MANIFEST = "/data/manifest.json"

# Assure les dossiers nécessaires
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DEST_DUMPS, exist_ok=True)
os.makedirs(DEST_FILESTORE, exist_ok=True)

@app.route("/")
def index():
    here = os.path.dirname(__file__)
    return open(os.path.join(here, 'index.html'), 'r', encoding='utf-8').read()

@app.route("/upload", methods=["POST"])
def upload_zip():
    zip_obj = request.files.get('zipfile')
    if not zip_obj or not zip_obj.filename.lower().endswith('.zip'):
        return "⚠️ Merci de déposer un fichier .zip valide."

    archive_path = os.path.join(UPLOAD_DIR, 'archive.zip')
    zip_obj.save(archive_path)

    tmp_dir = os.path.join(UPLOAD_DIR, 'extracted')
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    with zipfile.ZipFile(archive_path, 'r') as z:
        z.extractall(tmp_dir)

    # dump.sql obligatoire
    dump_src = os.path.join(tmp_dir, 'dump.sql')
    if not os.path.isfile(dump_src):
        return "❌ dump.sql manquant dans l'archive."
    shutil.copy2(dump_src, os.path.join(DEST_DUMPS, 'dump.sql'))

    # filestore facultatif
    fs_src = os.path.join(tmp_dir, 'filestore')
    shutil.rmtree(DEST_FILESTORE, ignore_errors=True)
    if os.path.isdir(fs_src):
        shutil.copytree(fs_src, DEST_FILESTORE)

    # manifest facultatif
    mf_src = os.path.join(tmp_dir, 'manifest.json')
    if os.path.isfile(mf_src):
        shutil.copy2(mf_src, DEST_MANIFEST)

    # Lancer migration en arrière-plan
    try:
        subprocess.Popen(["python3", "/app/scripts/run_migration.py"])
    except Exception as e:
        return f"❌ Erreur lancement migration : {e}"

    return "✅ Migration lancée ! Suivez les logs via Docker."

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)