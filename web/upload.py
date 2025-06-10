@app.route("/upload", methods=["POST"])
def upload_zip():
    zipfile_obj = request.files.get('zipfile')
    if not zipfile_obj or not zipfile_obj.filename.endswith(".zip"):
        return "⚠️ Merci de déposer une archive .zip valide."

    zip_path = os.path.join(UPLOAD_DIR, "archive.zip")
    zipfile_obj.save(zip_path)

    # Préparer le dossier d'extraction
    extract_dir = os.path.join(UPLOAD_DIR, "extracted")
    shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)

    # Décompresser
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    # Copier dump.sql
    dump_src = os.path.join(extract_dir, "dump.sql")
    if not os.path.exists(dump_src):
        return "❌ Le fichier dump.sql est manquant dans l’archive."
    os.makedirs(DEST_DUMPS, exist_ok=True)
    shutil.copy2(dump_src, DUMP_TARGET)
    print("✅ dump.sql copié.")

    # Copier filestore
    fs_src = os.path.join(extract_dir, "filestore")
    if os.path.exists(fs_src):
        # Supprimer l'ancien filestore s'il existe
        shutil.rmtree(DEST_FILESTORE, ignore_errors=True)
        # Copier le nouveau
        shutil.copytree(fs_src, DEST_FILESTORE)
        print("✅ filestore copié.")
    else:
        print("⚠️ Aucun filestore détecté dans l’archive.")

    # Copier manifest.json s'il existe
    manifest_src = os.path.join(extract_dir, "manifest.json")
    if os.path.exists(manifest_src):
        shutil.copy2(manifest_src, DEST_MANIFEST)
        print("✅ manifest.json copié.")
    else:
        print("ℹ️ Aucun manifest.json fourni.")

    # Lancer la migration
    try:
        subprocess.Popen(["python3", "/app/scripts/run_migration.py"])
        return "✅ Archive traitée. La migration a été lancée. Suivez les logs via Docker."
    except Exception as e:
        return f"❌ Erreur lors du lancement de la migration : {e}"
