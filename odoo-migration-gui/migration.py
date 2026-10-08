#!/usr/bin/env python3
"""Assistant local Odoo Community / OpenUpgrade. Python 3.9+, Docker et Git."""
import argparse
import ast
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
METRICS = {
    "contacts": "SELECT count(*) FROM res_partner",
    "utilisateurs": "SELECT count(*) FROM res_users",
    "societes": "SELECT count(*) FROM res_company",
    "ventes": "SELECT count(*) FROM sale_order",
    "employes": "SELECT count(*) FROM hr_employee",
    "ecritures": "SELECT count(*) FROM account_move_line",
    "debit": "SELECT coalesce(sum(debit),0)::text FROM account_move_line",
    "credit": "SELECT coalesce(sum(credit),0)::text FROM account_move_line",
    "pieces_jointes": "SELECT count(*) FROM ir_attachment WHERE store_fname IS NOT NULL",
}
MODULE_SQL = "SELECT coalesce(json_agg(x),'[]') FROM (SELECT name, state, latest_version FROM ir_module_module WHERE state != 'uninstalled' ORDER BY name) x"


def safe_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("Nom invalide (lettres, chiffres, tirets et points uniquement).")
    return value


def write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class Engine:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.lock = threading.Lock()
        self.logs = []
        self.busy = False
        self.progress = "Prêt"
        self.state = {"version": 12, "validated": True, "backup": None,
                      "prepared": None, "pending": None, "error": None}
        self.statefile = self.root / "state.json"
        if self.statefile.exists():
            self.state.update(json.loads(self.statefile.read_text()))
        self.source = {"app": "odoo12", "db": "odoo12-db", "database": "NicolasAllanot"}

    def save(self):
        write_json(self.statefile, self.state)

    def log(self, message):
        line = time.strftime("%H:%M:%S ") + message
        self.logs.append(line)
        self.logs = self.logs[-1200:]
        with (self.root / "assistant.log").open("a") as f:
            f.write(line + "\n")

    def run(self, args, *, stdin=None, out=None, quiet=False, timeout=7200):
        # Pas de shell, pas d'interpolation de commandes utilisateur.
        if not quiet:
            self.log("Exécution : " + " ".join(str(a) for a in args))
            self.progress = " ".join(str(a) for a in args[:5])
        if out:
            with Path(out).open("wb") as f:
                p = subprocess.run(args, input=stdin, stdout=f, stderr=subprocess.PIPE, timeout=timeout)
            if p.returncode:
                raise RuntimeError(p.stderr.decode(errors="replace")[-6000:])
            return ""
        p = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # communicate évite les blocages de pipes; le journal complet reste sur disque.
        try:
            data, _ = p.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            raise RuntimeError("Délai dépassé. Vérifier les conteneurs de travail dans Docker.")
        text = data.decode(errors="replace")
        if not quiet and text:
            self.log(text[-12000:])
        if p.returncode:
            raise RuntimeError(text[-6000:] or "Commande Docker/Git en échec")
        return text.strip()

    def inspect(self, name):
        return json.loads(self.run(["docker", "inspect", safe_name(name)], quiet=True))[0]

    def sql(self, container, db, sql):
        # Identifiants PostgreSQL déduits dans le conteneur, jamais affichés.
        return self.run(["docker", "exec", "-i", container, "sh", "-c",
                         'exec psql -X -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-odoo}" -d "$1" -At',
                         "sh", db], stdin=sql.encode(), quiet=True)

    def modules(self, container, db):
        return json.loads(self.sql(container, db, MODULE_SQL))

    def metrics(self, container, db):
        result = {}
        for label, query in METRICS.items():
            table = re.search(r"FROM (\w+)", query).group(1)
            present = self.sql(container, db, "SELECT to_regclass('public." + table + "') IS NOT NULL")
            result[label] = self.sql(container, db, query) if present == "t" else None
        return result

    def audit(self, payload):
        source = {k: safe_name(payload.get(k, v)) for k, v in self.source.items()}
        app, db = self.inspect(source["app"]), self.inspect(source["db"])
        if not app["State"]["Running"] or not db["State"]["Running"]:
            raise ValueError("Les deux conteneurs source doivent être démarrés.")
        version = self.sql(source["db"], source["database"],
                           "SELECT latest_version FROM ir_module_module WHERE name='base'")
        if not version.startswith("12."):
            raise ValueError("La source doit être Odoo 12. Version détectée : " + version)
        if self.state["backup"] and source != self.state.get("source"):
            raise ValueError("Cette session est déjà associée à une source. Utiliser un nouveau --workdir.")
        self.source = source
        paths = self.run(["docker", "exec", source["app"], "python3", "-c",
            "import configparser,json; c=configparser.ConfigParser(); c.read('/etc/odoo/odoo.conf'); "
            "print(json.dumps({k:c.get('options',k,fallback='') for k in ('addons_path','data_dir')}))"], quiet=True)
        config = json.loads(paths)
        addons = [p.strip() for p in config["addons_path"].split(",") if p.strip()]
        for default in ["/mnt/extra-addons", "/usr/lib/python3/dist-packages/odoo/addons"]:
            if default not in addons:
                addons.append(default)
        report = {"source": source, "image": app["Config"]["Image"],
                  "postgres": db["Config"]["Image"], "version": version,
                  "modules": self.modules(source["db"], source["database"]),
                  "mounts": [{"volume": m.get("Name", "bind"), "destination": m["Destination"]} for m in app["Mounts"]],
                  "addons_paths": addons, "data_dir": config["data_dir"] or "/var/lib/odoo",
                  "disk_free_gib": round(shutil.disk_usage(self.root).free / 2**30, 1)}
        self.state["audit"] = report
        self.state["source"] = source
        self.save()
        self.log("Diagnostic terminé : " + str(len(report["modules"])) + " modules actifs.")

    def dump(self, container, db, target):
        self.run(["docker", "exec", container, "sh", "-c",
                  'exec pg_dump -U "${POSTGRES_USER:-odoo}" -Fc --no-owner --no-acl -d "$1"',
                  "sh", db], out=target, quiet=True)
        with Path(target).open("rb") as f:
            if f.read(5) != b"PGDMP":
                raise RuntimeError("Le fichier produit n'est pas un dump PostgreSQL.")

    def backup(self, payload):
        if not payload.get("ack_pause"):
            raise ValueError("Cocher l'autorisation du bref arrêt d'Odoo 12.")
        if self.state["backup"]:
            raise ValueError("Sauvegarde déjà créée. Nouveau --workdir pour une nouvelle capture de production.")
        if not self.state.get("audit"):
            raise ValueError("Lancer d'abord le diagnostic.")
        source = self.state["source"]
        folder = self.root / "backups" / time.strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, mode=0o700)
        if not self.inspect(source["app"])["State"]["Running"]:
            raise ValueError("Odoo source doit être démarré avant cette opération.")
        stopped = False
        complete = False
        try:
            self.log("Arrêt temporaire d'Odoo 12 pour figer la base et les pièces jointes.")
            self.run(["docker", "stop", "--time", "90", source["app"]])
            stopped = True
            # Des écritures extérieures à Odoo ne doivent pas viser ces bases pendant cette capture.
            dbs = self.sql(source["db"], "postgres",
                           "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname != 'postgres'").splitlines()
            dumps = {}
            for index, db in enumerate(dbs):
                path = folder / ("database-" + str(index) + ".dump")
                self.dump(source["db"], db, path)
                dumps[db] = path.name
            if source["database"] not in dumps:
                raise RuntimeError("Base principale absente de la sauvegarde.")
            self.run(["docker", "cp", source["app"] + ":" + self.state["audit"]["data_dir"] + "/.", str(folder / "data")])
            self.run(["docker", "cp", source["app"] + ":/etc/odoo/.", str(folder / "config")])
            addon_paths = []
            for i, path in enumerate(self.state["audit"]["addons_paths"]):
                dest = folder / ("addons-" + str(i))
                try:
                    self.run(["docker", "cp", source["app"] + ":" + path + "/.", str(dest)], quiet=True)
                    addon_paths.append({"source": path, "copy": dest.name})
                except RuntimeError as error:
                    # Un chemin configuré absent est une anomalie, jamais une sauvegarde complète.
                    if path in ["/usr/lib/python3/dist-packages/odoo/addons", "/mnt/extra-addons"]:
                        self.log("Chemin standard absent : " + path)
                    else:
                        raise error
            write_json(folder / "container-private.json", [self.inspect(source["app"]), self.inspect(source["db"])])
            write_json(folder / "docker-diff.json", self.run(["docker", "diff", source["app"]], quiet=True))
            # L'export préserve les modifications hors volumes et les dépendances locales.
            self.run(["docker", "export", source["app"]], out=folder / "container-rootfs.tar", quiet=True)
            metrics = self.metrics(source["db"], source["database"])
            modules = self.modules(source["db"], source["database"])
            manifest = {"source": source, "dumps": dumps, "addons": addon_paths,
                        "metrics": metrics, "modules": modules}
            hashes = {}
            for path in folder.rglob("*"):
                if path.is_file():
                    h = hashlib.sha256()
                    with path.open("rb") as f:
                        for block in iter(lambda: f.read(1024*1024), b""):
                            h.update(block)
                    hashes[str(path.relative_to(folder))] = h.hexdigest()
            manifest["sha256"] = hashes
            write_json(folder / "manifest.json", manifest)
            complete = True
        finally:
            if stopped:
                self.run(["docker", "start", source["app"]])
                self.log("Odoo 12 redémarré.")
        if complete:
            self.state["backup"] = str(folder)
            self.state["checkpoint"] = str(folder / dumps[source["database"]])
            self.state["data"] = str(folder / "data")
            self.state["metrics"] = metrics
            self.state["modules"] = modules
            self.save()
            self.log("Sauvegarde capturée. La restauration sera testée sur PostgreSQL isolé avant migration.")

    def git_clone(self, url, branch, path):
        if not path.exists():
            self.run(["git", "clone", "--depth", "1", "--branch", branch, url, str(path)])
        return self.run(["git", "-C", str(path), "rev-parse", "HEAD"], quiet=True)

    def prepare(self, payload):
        version = self.state["version"] + 1
        if version > 19:
            raise ValueError("Version 19 atteinte.")
        if not self.state["backup"] or not self.state["validated"]:
            raise ValueError("Une sauvegarde et la validation de l'étape précédente sont nécessaires.")
        folder = self.root / "build" / str(version)
        self.state["prepared"] = None
        self.save()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "extra").mkdir(exist_ok=True)
        self.log("Préparation " + str(version-1) + " → " + str(version))
        ouhash = self.git_clone("https://github.com/OCA/OpenUpgrade.git", str(version) + ".0", folder / "OpenUpgrade")
        libhash = self.git_clone("https://github.com/OCA/openupgradelib.git", "master", folder / "openupgradelib")
        # Répertoires extra/<version>/repo/module ou extra/<version>/module.
        external = self.root / "extra" / str(version)
        if external.exists():
            shutil.rmtree(folder / "extra")
            shutil.copytree(external, folder / "extra")
        dockerfile = '''FROM odoo:VERSION
USER root
COPY OpenUpgrade /opt/OpenUpgrade
COPY openupgradelib /opt/openupgradelib
COPY extra /opt/extra
RUN python3 -m pip install --no-cache-dir --target /opt/oudeps /opt/openupgradelib
ENV PYTHONPATH=/opt/oudeps
USER odoo
'''.replace("VERSION", str(version))
        if (folder / "Dockerfile.custom").exists():
            dockerfile = (folder / "Dockerfile.custom").read_text()
        (folder / "Dockerfile").write_text(dockerfile)
        image = "odoo-migration-local:" + str(version)
        self.run(["docker", "build", "-t", image, str(folder)])
        image_id = self.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], quiet=True)
        # Inventaire réel de l'image cible, sans accès à la production.
        script = """import ast,json,pathlib
roots=['/opt/OpenUpgrade/addons','/opt/OpenUpgrade/odoo/addons'] if VERSION == 13 else ['/usr/lib/python3/dist-packages/odoo/addons','/opt/OpenUpgrade']
roots += ['/opt/extra']
mods={}; paths=set(roots)
for root in roots:
 for p in pathlib.Path(root).rglob('__manifest__.py'):
  try:
   m=ast.literal_eval(p.read_text()); name=p.parent.name
   if not m.get('installable',True): continue
   mods[name]={'version':m.get('version',''),'depends':m.get('depends',[]),'external':str(p).startswith('/opt/extra/'),'scripts':(p.parent/'migrations').exists() or (p.parent/'upgrades').exists()}
   paths.add(str(p.parent.parent))
  except Exception: pass
print(json.dumps({'modules':mods,'paths':sorted(paths)}))
""".replace("VERSION", str(version))
        inventory = json.loads(self.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python3", image_id, "-c", script], quiet=True))
        installed = [m["name"] for m in self.state["modules"]]
        missing = sorted(set(installed) - set(inventory["modules"]))
        external_modules = [m for m in installed if inventory["modules"].get(m, {}).get("external")]
        # Source des fusions/renommages connue pour les branches modernes.
        apriori = {}
        for file in (folder / "OpenUpgrade").rglob("apriori.py"):
            try:
                for node in ast.parse(file.read_text()).body:
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id in ("renamed_modules", "merged_modules"):
                                apriori[target.id] = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                pass
        prepared = {"target": version, "image": image_id, "image_tag": image, "folder": str(folder),
                    "commits": {"OpenUpgrade": ouhash, "openupgradelib": libhash},
                    "inventory": inventory, "missing": missing, "external": external_modules,
                    "apriori": apriori,
                    "coverage_url": "https://oca.github.io/OpenUpgrade/coverage_analysis/modules" + str(version-1) + "0-" + str(version) + "0.html"}
        write_json(folder / "prepared.json", prepared)
        self.state["prepared"] = prepared
        self.save()
        self.log("Image prête. Modules absents : " + (", ".join(missing) or "aucun") + ". Vérifier la couverture de tous les modules.")

    def resource(self, kind, name):
        # Un nom existant ne sera jamais réutilisé, même s'il ressemble au préfixe.
        self.run(["docker", kind, "inspect", name], quiet=True)

    def start_pg(self, version, suffix):
        prefix = "omig-" + suffix
        network, db, volume = prefix + "-net", prefix + "-db", prefix + "-pg"
        self.run(["docker", "network", "create", "--internal", "--label", "odoo.migration=local", network])
        self.run(["docker", "volume", "create", "--label", "odoo.migration=local", volume])
        env = self.root / (prefix + ".env")
        env.write_text("POSTGRES_USER=odoo\nPOSTGRES_DB=migration\nPOSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n")
        os.chmod(env, 0o600)
        self.run(["docker", "run", "-d", "--name", db, "--label", "odoo.migration=local",
                  "--network", network, "--network-alias", "db", "--env-file", str(env),
                  "-v", volume + ":/var/lib/postgresql/data", "postgres:" + ("14" if version <= 15 else "16")])
        for _ in range(90):
            try:
                self.run(["docker", "exec", db, "pg_isready", "-U", "odoo", "-d", "migration"], quiet=True, timeout=10)
                break
            except RuntimeError:
                time.sleep(1)
        else:
            raise RuntimeError("PostgreSQL isolé ne démarre pas : " + db)
        password = env.read_text().split("POSTGRES_PASSWORD=", 1)[1].strip()
        return {"network": network, "db": db, "pg_volume": volume, "password": password, "prefix": prefix}

    def restore(self, work):
        checkpoint = Path(self.state["checkpoint"])
        # Vérifier l'intégrité du dump avant toute restauration.
        if self.state["version"] == 12:
            manifest = json.loads((Path(self.state["backup"]) / "manifest.json").read_text())
            expected = manifest["sha256"][checkpoint.name]
        else:
            expected = json.loads((checkpoint.parent / "validation.json").read_text())["dump_sha256"]
        h = hashlib.sha256()
        with checkpoint.open("rb") as f:
            for block in iter(lambda: f.read(1024*1024), b""):
                h.update(block)
        if h.hexdigest() != expected:
            raise RuntimeError("Somme SHA-256 du point de contrôle incorrecte. Restauration bloquée.")
        self.run(["docker", "cp", str(checkpoint), work["db"] + ":/tmp/source.dump"])
        self.run(["docker", "exec", work["db"], "pg_restore", "--list", "/tmp/source.dump"], quiet=True)
        self.run(["docker", "exec", work["db"], "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
                  "-U", "odoo", "-d", "migration", "/tmp/source.dump"])
        metrics = self.metrics(work["db"], "migration")
        if metrics != self.state["metrics"]:
            raise RuntimeError("La copie restaurée diffère du point de contrôle. Migration arrêtée.")
        self.log("Restauration réelle validée : compteurs et totaux identiques au point de contrôle.")
        # Renommer les chemins du filestore : le nom de la base test est 'migration'.
        data = self.root / "runs" / work["prefix"] / "data"
        shutil.copytree(self.state["data"], data)
        filestore = data / "filestore"
        source_name = self.state["source"]["database"] if self.state["version"] == 12 else "migration"
        if (filestore / source_name).exists() and source_name != "migration":
            if (filestore / "migration").exists():
                raise RuntimeError("Filestore migration déjà présent dans la source.")
            (filestore / source_name).rename(filestore / "migration")
        data_volume = work["prefix"] + "-data"
        self.run(["docker", "volume", "create", "--label", "odoo.migration=local", data_volume])
        helper = work["prefix"] + "-copy"
        self.run(["docker", "create", "--name", helper, "--network", "none", "--user", "root",
                  "--label", "odoo.migration=local", "-v", data_volume + ":/var/lib/odoo",
                  "--entrypoint", "sh", self.state["prepared"]["image"], "-c", "chown -R odoo:odoo /var/lib/odoo"])
        self.run(["docker", "cp", str(data) + "/.", helper + ":/var/lib/odoo"])
        self.run(["docker", "start", "-a", helper])
        self.run(["docker", "rm", helper])
        work["data_volume"] = data_volume
        work["data_dir"] = str(data)
        # Neutralisation sur la copie uniquement, avant chargement du registre Odoo.
        neutralize = "UPDATE ir_cron SET active=false;"
        for table in ("ir_mail_server", "fetchmail_server"):
            if self.sql(work["db"], "migration", "SELECT to_regclass('public." + table + "') IS NOT NULL") == "t":
                neutralize += "UPDATE " + table + " SET active=false;"
        self.sql(work["db"], "migration", neutralize)
        return metrics

    def config(self, work):
        prepared = self.state["prepared"]
        folder = self.root / "runs" / work["prefix"]
        folder.mkdir(parents=True, exist_ok=True)
        conf = folder / "odoo.conf"
        conf.write_text("[options]\nadmin_passwd = " + secrets.token_hex(24) +
            "\ndb_host = db\ndb_port = 5432\ndb_user = odoo\ndb_password = " + work["password"] +
            "\ndb_name = migration\ndbfilter = ^migration$\nlist_db = False\n" +
            "data_dir = /var/lib/odoo\naddons_path = " + ",".join(prepared["inventory"]["paths"]) +
            "\nworkers = 0\nmax_cron_threads = 0\nhttp_interface = 0.0.0.0\n" +
            "limit_time_real = 7200\nlimit_time_cpu = 7200\n")
        os.chmod(conf, 0o600)
        # Docker cp puis chown évite de rendre le secret lisible sur l'hôte.
        work["config"] = str(conf)

    def app_create(self, work, name, migration=True, mappings=None):
        prepared = self.state["prepared"]
        v = prepared["target"]
        command = ["python3", "/opt/OpenUpgrade/odoo-bin"] if v == 13 else ["odoo"]
        command += ["-c", "/tmp/migration.conf", "--database", "migration", "--max-cron-threads", "0"]
        if migration:
            command += ["--update", "all", "--stop-after-init", "--no-http"]
            if v >= 14:
                command += ["--load=base,web,openupgrade_framework", "--upgrade-path=/opt/OpenUpgrade/openupgrade_scripts/scripts"]
        args = ["docker", "create", "--name", name, "--label", "odoo.migration=local",
                "--network", work["network"], "-v", work["data_volume"] + ":/var/lib/odoo",
                "-e", "OPENUPGRADE_TARGET_VERSION=" + str(v) + ".0"]
        if mappings:
            args += ["-e", "OPENUPGRADE_RENAMED_MODULES=" + json.dumps(mappings.get("rename", {})),
                     "-e", "OPENUPGRADE_MERGED_MODULES=" + json.dumps(mappings.get("merge", {}))]
        if not migration:
            args += ["-p", "127.0.0.1:18069:8069"]
        # La configuration 0600 reste privée sur l'hôte. Le lanceur root en donne
        # la propriété à odoo, puis abandonne root AVANT de lancer le serveur.
        launcher = "import os,pwd,sys; u=pwd.getpwnam('odoo'); os.chown('/tmp/migration.conf',u.pw_uid,u.pw_gid); os.initgroups('odoo',u.pw_gid); os.setgid(u.pw_gid); os.setuid(u.pw_uid); os.execvp(sys.argv[1],sys.argv[1:])"
        args += ["--user", "root", "--entrypoint", "python3", prepared["image"], "-c", launcher] + command
        self.run(args)
        self.run(["docker", "cp", work["config"], name + ":/tmp/migration.conf"])
        self.run(["docker", "start", name])

    def migrate(self, payload):
        prepared = self.state["prepared"]
        if not prepared or prepared["target"] != self.state["version"] + 1:
            raise ValueError("Préparer l'image de l'étape suivante.")
        if not self.state["validated"]:
            raise ValueError("Valider l'étape précédente.")
        running = self.run(["docker", "ps", "--filter", "label=odoo.migration=local", "--filter", "name=-upgrade", "--format", "{{.Names}}"], quiet=True)
        if running:
            raise ValueError("Un processus de migration tourne encore : " + running + ". Attendre sa fin avant une relance.")
        if not payload.get("ack_coverage"):
            raise ValueError("Vérifier et confirmer la couverture OpenUpgrade et les modules tiers.")
        decisions = payload.get("decisions", {})
        if not isinstance(decisions, dict):
            raise ValueError("Les décisions doivent être un objet JSON.")
        mappings = {"rename": {}, "merge": {}}
        for module in prepared["missing"]:
            item = decisions.get(module, {})
            if not isinstance(item, dict) or len(str(item.get("evidence", "")).strip()) < 15:
                raise ValueError("Décision documentée requise pour le module absent : " + module)
            mode = item.get("action")
            if mode == "openupgrade":
                continue
            if mode not in mappings or prepared["target"] < 14:
                raise ValueError("Action autorisée : openupgrade (ou rename/merge à partir de 14). Aucun module n'est désinstallé.")
            target = item.get("target")
            if target not in prepared["inventory"]["modules"]:
                raise ValueError("Module cible absent : " + str(target))
            mappings[mode][module] = target
        suffix = time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
        work = self.start_pg(prepared["target"], suffix)
        # Mot de passe jamais écrit dans state.json ni envoyé au navigateur.
        try:
            self.restore(work)
            self.config(work)
            name = work["prefix"] + "-upgrade"
            self.app_create(work, name, mappings=mappings)
            self.log("Migration en cours. Le journal Odoo complet sera enregistré dans le dossier de l'essai.")
            status = self.run(["docker", "wait", name], quiet=True)
            logfile = self.root / "runs" / work["prefix"] / "migration.log"
            logfile.write_text(self.run(["docker", "logs", name], quiet=True))
            self.log(logfile.read_text(errors="replace")[-12000:])
            if status != "0":
                raise RuntimeError("OpenUpgrade a échoué (code " + status + "). Point de contrôle précédent conservé.")
            actual = self.sql(work["db"], "migration", "SELECT latest_version FROM ir_module_module WHERE name='base'")
            if not actual.startswith(str(prepared["target"]) + "."):
                raise RuntimeError("Version réelle de base inattendue : " + actual)
            unfinished = self.sql(work["db"], "migration", "SELECT name || ': ' || state FROM ir_module_module WHERE state IN ('to upgrade','to install','to remove')")
            if unfinished:
                raise RuntimeError("Modules non finalisés : " + unfinished)
            metrics = self.metrics(work["db"], "migration")
            work.pop("password", None)
            self.state["pending"] = {"work": work, "version": prepared["target"], "metrics": metrics,
                "before": self.state["metrics"], "modules": self.modules(work["db"], "migration"),
                "decisions": decisions, "commits": prepared["commits"], "image": prepared["image"]}
            self.state["validated"] = False
            self.save()
            self.log("Migration technique terminée. Ouvrir la copie et contrôler les données avant validation.")
        except Exception:
            self.log("Essai conservé pour diagnostic : " + work["prefix"] + ". Une relance repartira du dernier point validé.")
            raise

    def preview(self, payload):
        pending = self.state["pending"]
        if not pending:
            raise ValueError("Aucune migration à examiner.")
        work = pending["work"]
        name = work["prefix"] + "-preview"
        try:
            info = self.inspect(name)
            if not info["State"]["Running"]:
                self.run(["docker", "start", name])
        except RuntimeError:
            self.app_create(work, name, migration=False)
        self.log("Copie de test : http://127.0.0.1:18069 (accès par tunnel SSH). Connexion avec les comptes copiés.")

    def validate(self, payload):
        pending = self.state["pending"]
        if not pending or not payload.get("ack_business"):
            raise ValueError("Contrôler factures, ventes, pièces jointes et comptes avant de valider.")
        note = str(payload.get("validation_note", "")).strip()
        if len(note) < 15:
            raise ValueError("Inscrire une note de validation (au moins 15 caractères).")
        work = pending["work"]
        preview = work["prefix"] + "-preview"
        try:
            self.inspect(preview)
        except RuntimeError:
            pass
        else:
            self.run(["docker", "stop", preview])
        self.sql(work["db"], "migration", "UPDATE ir_cron SET active=false;")
        # Détecter les pièces jointes dont le fichier est absent dans la copie.
        self.run(["docker", "cp", work["prefix"] + "-upgrade:/var/lib/odoo/.", work["data_dir"]])
        filenames = self.sql(work["db"], "migration", "SELECT DISTINCT store_fname FROM ir_attachment WHERE store_fname IS NOT NULL").splitlines()
        filestore = Path(work["data_dir"]) / "filestore" / "migration"
        missing = [f for f in filenames if not (filestore / f).is_file()]
        if missing:
            raise RuntimeError(str(len(missing)) + " fichiers de pièces jointes absents. Validation bloquée.")
        checkpoint_folder = self.root / "checkpoints" / (str(pending["version"]) + "-" + work["prefix"])
        checkpoint_folder.mkdir(parents=True)
        dump = checkpoint_folder / "database.dump"
        self.dump(work["db"], "migration", dump)
        data = checkpoint_folder / "data"
        shutil.copytree(work["data_dir"], data)
        metrics = self.metrics(work["db"], "migration")
        validation = {"version": pending["version"], "note": note, "before": pending["before"],
                      "after": metrics, "decisions": pending["decisions"], "commits": pending["commits"],
                      "image": pending["image"], "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest()}
        write_json(checkpoint_folder / "validation.json", validation)
        self.state.update(version=pending["version"], checkpoint=str(dump), data=str(data),
            validated=True, metrics=metrics, modules=self.modules(work["db"], "migration"), pending=None, prepared=None)
        self.save()
        self.log("Version " + str(self.state["version"]) + " validée et sauvegardée. La production Odoo 12 reste en place.")

    def retry(self, payload):
        if not self.state["pending"]:
            raise ValueError("Aucun essai à rejeter.")
        name = self.state["pending"]["work"]["prefix"] + "-preview"
        try:
            self.inspect(name)
        except RuntimeError:
            pass
        else:
            self.run(["docker", "stop", name])
        self.state["pending"] = None
        self.state["validated"] = True
        self.save()
        self.log("Essai rejeté. La prochaine migration repartira du point validé précédent.")

    def dispatch(self, action, payload):
        if action not in ("audit", "backup", "prepare", "migrate", "preview", "validate", "retry"):
            raise ValueError("Action inconnue")
        if not self.lock.acquire(blocking=False):
            raise ValueError("Une opération est déjà en cours.")
        self.busy = True
        self.state["error"] = None
        def worker():
            try:
                getattr(self, action)(payload)
            except Exception as error:
                self.state["error"] = str(error)
                self.log("ÉCHEC : " + str(error))
            finally:
                self.busy = False
                self.save()
                self.lock.release()
        threading.Thread(target=worker, daemon=False).start()

    def public(self):
        return {"busy": self.busy, "progress": self.progress, "state": self.state, "logs": self.logs[-140:], "workdir": str(self.root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=str(HERE / "work"))
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    os.umask(0o077)
    # Empêche deux assistants de manipuler la même session.
    import fcntl
    engine = Engine(args.workdir)
    instance_lock = (engine.root / "assistant.lock").open("w")
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Un assistant utilise déjà ce dossier.")
    token = secrets.token_urlsafe(32)
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Ne pas journaliser le jeton de session.

        def send(self, code, data, mime="application/json"):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            host = self.headers.get("Host", "")
            if host not in ("127.0.0.1:" + str(args.port), "localhost:" + str(args.port)):
                return False
            return secrets.compare_digest(self.headers.get("X-Session", ""), token)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path)
            if path.path == "/" and self.headers.get("Host") in ("127.0.0.1:" + str(args.port), "localhost:" + str(args.port)):
                self.send(200, (HERE / "interface.html").read_bytes(), "text/html")
            elif path.path == "/api/state" and self.authorized():
                self.send(200, engine.public())
            else:
                self.send(403, {"error": "Accès refusé. Utiliser le lien affiché dans le terminal."})

        def do_POST(self):
            if not self.authorized():
                self.send(403, {"error": "Session invalide"})
                return
            origin = self.headers.get("Origin")
            if origin not in (None, "http://127.0.0.1:" + str(args.port), "http://localhost:" + str(args.port)):
                self.send(403, {"error": "Origine refusée"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100000:
                    raise ValueError("Taille invalide")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Objet JSON requis")
                action = urllib.parse.urlparse(self.path).path.removeprefix("/api/")
                engine.dispatch(action, payload)
                self.send(202, {"ok": True})
            except (ValueError, json.JSONDecodeError) as error:
                self.send(400, {"error": str(error)})
    print("Assistant : http://127.0.0.1:" + str(args.port) + "/#" + token, flush=True)
    print("Pour un accès distant : tunnel SSH vers les ports " + str(args.port) + " et 18069.", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
