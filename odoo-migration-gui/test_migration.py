import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from migration import Engine, safe_name, write_json


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(self.tmp.name)

    def test_names_reject_shell_and_docker_options(self):
        for value in ['$(id)', '-x', 'test;id', '../file', 'name\nother', '', None]:
            with self.assertRaises(ValueError):
                safe_name(value)
        self.assertEqual(safe_name('NicolasAllanot'), 'NicolasAllanot')

    def test_private_state_resumes_checkpoint(self):
        self.engine.state.update(version=15, checkpoint='/private/example.dump')
        self.engine.save()
        resumed = Engine(self.tmp.name)
        self.assertEqual(resumed.state['version'], 15)
        self.assertEqual(resumed.state['checkpoint'], '/private/example.dump')
        self.assertEqual(os.stat(self.engine.statefile).st_mode & 0o777, 0o600)

    def test_missing_module_cannot_be_silently_ignored(self):
        self.engine.state['prepared'] = {'target': 13, 'missing': ['om_account_asset']}
        with patch.object(self.engine, 'run', return_value=''), patch.object(self.engine, 'start_pg') as pg:
            with self.assertRaisesRegex(ValueError, 'om_account_asset'):
                self.engine.migrate({'ack_coverage': True})
            pg.assert_not_called()

    def test_autopilot_stops_before_migration_on_missing_module(self):
        self.engine.state.update(audit={'modules': []}, backup='/backup', version=12,
            prepared={'target': 13, 'missing': ['om_account_asset'], 'external': []})
        with patch.object(self.engine, 'migrate') as migrate, patch.object(self.engine, 'prepare') as prepare:
            with self.assertRaisesRegex(ValueError, 'om_account_asset'):
                self.engine.autopilot({'ack_auto': True})
            migrate.assert_not_called()
            prepare.assert_called_once()  # Rebuild après ajout éventuel d'addons.
        self.assertFalse(self.engine.state['automation']['running'])
        self.assertIn('om_account_asset', self.engine.state['automation']['blocker'])

    def test_autopilot_stops_on_changed_accounting_totals(self):
        self.engine.state.update(audit={'modules': []}, backup='/backup', version=12,
            modules=[{'name': 'base', 'state': 'installed'}], metrics={'debit': '100'},
            pending={'version': 13, 'before': {'debit': '100'}, 'metrics': {'debit': '98'},
                     'modules': [{'name': 'base', 'state': 'installed'}]})
        with patch.object(self.engine, 'validate') as validate:
            with self.assertRaisesRegex(ValueError, 'debit'):
                self.engine.autopilot({'ack_auto': True})
            validate.assert_not_called()
        self.assertEqual(self.engine.state['version'], 12)

    def test_autopilot_finishes_last_step_and_opens_copy(self):
        self.engine.state.update(audit={'modules': []}, backup='/backup', version=18,
            modules=[{'name': 'base', 'state': 'installed'}], metrics={'contacts': '10'},
            prepared={'target': 19, 'missing': [], 'external': []}, pending=None)
        def simulate_migration(payload):
            self.engine.state['pending'] = {'version': 19, 'before': {'contacts': '10'},
                'metrics': {'contacts': '10'}, 'modules': [{'name': 'base', 'state': 'installed'}],
                'work': {'prefix': 'omig-test'}}
        def simulate_validation(payload):
            self.assertTrue(payload.get('_technical_auto'))
            self.engine.state.update(version=19, pending=None)
        with patch.object(self.engine, 'migrate', side_effect=simulate_migration) as migrate, \
             patch.object(self.engine, 'validate', side_effect=simulate_validation) as validate, \
             patch.object(self.engine, 'preview') as preview, \
             patch.object(self.engine, 'run', return_value='') as run:
            self.engine.autopilot({'ack_auto': True})
        migrate.assert_called_once()
        validate.assert_called_once()
        preview.assert_called_once()
        run.assert_called_with(['docker', 'start', 'omig-test-preview'])
        self.assertIsNone(self.engine.state['automation']['blocker'])

    def test_interrupted_automation_is_marked_for_review(self):
        self.engine.state['automation'] = {'running': True, 'stage': 'Migration Odoo 13', 'blocker': None}
        self.engine.save()
        restarted = Engine(self.tmp.name)
        self.assertFalse(restarted.state['automation']['running'])
        self.assertIn('redémarré', restarted.state['automation']['blocker'])

    def test_coverage_acknowledgement_required(self):
        self.engine.state['prepared'] = {'target': 13, 'missing': []}
        with patch.object(self.engine, 'run', return_value=''), patch.object(self.engine, 'start_pg') as pg:
            with self.assertRaisesRegex(ValueError, 'couverture'):
                self.engine.migrate({})
            pg.assert_not_called()

    def test_previous_validation_required(self):
        self.engine.state.update(prepared={'target': 13}, validated=False)
        with self.assertRaisesRegex(ValueError, 'précédente'):
            self.engine.migrate({'ack_coverage': True})

    def test_living_upgrade_blocks_second_attempt(self):
        self.engine.state['prepared'] = {'target': 13}
        with patch.object(self.engine, 'run', return_value='omig-active-upgrade'):
            with self.assertRaisesRegex(ValueError, 'encore'):
                self.engine.migrate({'ack_coverage': True})

    def test_backup_failure_restarts_production(self):
        self.engine.state.update(source={'app': 'odoo12', 'db': 'odoo12-db', 'database': 'NicolasAllanot'},
                                 audit={'data_dir': '/var/lib/odoo', 'addons_paths': []})
        commands = []
        def run(args, **kwargs):
            commands.append(args)
            return ''
        with patch.object(self.engine, 'inspect', return_value={'State': {'Running': True}}), \
             patch.object(self.engine, 'run', side_effect=run), \
             patch.object(self.engine, 'sql', return_value='NicolasAllanot'), \
             patch.object(self.engine, 'dump', side_effect=RuntimeError('simulated backup failure')):
            with self.assertRaisesRegex(RuntimeError, 'simulated'):
                self.engine.backup({'ack_pause': True})
        self.assertIn(['docker', 'stop', '--time', '90', 'odoo12'], commands)
        self.assertEqual(commands[-1], ['docker', 'start', 'odoo12'])
        self.assertIsNone(self.engine.state['backup'])

    def test_corrupt_checkpoint_blocks_restore_before_docker(self):
        dump = Path(self.tmp.name) / 'database.dump'
        dump.write_bytes(b'corrupted')
        write_json(dump.parent / 'validation.json', {'dump_sha256': 'incorrect'})
        self.engine.state.update(version=13, checkpoint=str(dump))
        with patch.object(self.engine, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                self.engine.restore({'db': 'omig-test-db'})
            run.assert_not_called()

    def test_metrics_mismatch_blocks_migration(self):
        dump = Path(self.tmp.name) / 'database.dump'
        dump.write_bytes(b'PGDMP-example')
        write_json(dump.parent / 'validation.json', {'dump_sha256': hashlib.sha256(dump.read_bytes()).hexdigest()})
        self.engine.state.update(version=13, checkpoint=str(dump), metrics={'contacts': '10'})
        with patch.object(self.engine, 'run', return_value=''), \
             patch.object(self.engine, 'metrics', return_value={'contacts': '9'}):
            with self.assertRaisesRegex(RuntimeError, 'diffère'):
                self.engine.restore({'db': 'omig-test-db'})

    def test_modern_upgrade_uses_isolated_resources_and_correct_flags(self):
        self.engine.state['prepared'] = {'target': 19, 'image': 'sha256:pinned'}
        work = {'network': 'omig-test-net', 'data_volume': 'omig-test-data', 'config': '/private/test.conf'}
        with patch.object(self.engine, 'run', return_value='') as run:
            self.engine.app_create(work, 'omig-test-upgrade')
        args = run.call_args_list[0].args[0]
        self.assertIn('omig-test-net', args)
        self.assertIn('omig-test-data:/var/lib/odoo', args)
        self.assertIn('--upgrade-path=/opt/OpenUpgrade/openupgrade_scripts/scripts', args)
        self.assertIn('--load=base,web,openupgrade_framework', args)
        self.assertIn('--stop-after-init', args)
        self.assertNotIn('-p', args)
        self.assertNotIn('traefik_default', args)
        self.assertNotIn('odoo_12_odoo-web-data', ' '.join(args))
        self.assertEqual(run.call_args_list[1].args[0][:2], ['docker', 'cp'])
        self.assertEqual(run.call_args_list[2].args[0], ['docker', 'start', 'omig-test-upgrade'])

    def test_old_upgrade_uses_fork_without_framework(self):
        self.engine.state['prepared'] = {'target': 13, 'image': 'sha256:pinned'}
        work = {'network': 'omig-test-net', 'data_volume': 'omig-test-data', 'config': '/private/test.conf'}
        with patch.object(self.engine, 'run', return_value='') as run:
            self.engine.app_create(work, 'omig-test-upgrade')
        args = run.call_args_list[0].args[0]
        self.assertIn('/opt/OpenUpgrade/odoo-bin', args)
        self.assertNotIn('--load=base,web,openupgrade_framework', args)

    def test_preview_port_is_localhost(self):
        self.engine.state['prepared'] = {'target': 19, 'image': 'sha256:pinned'}
        work = {'network': 'omig-test-net', 'data_volume': 'omig-test-data', 'config': '/private/test.conf'}
        with patch.object(self.engine, 'run', return_value='') as run:
            self.engine.app_create(work, 'omig-test-preview', migration=False)
        args = run.call_args_list[0].args[0]
        self.assertIn('127.0.0.1:18069:8069', args)
        self.assertNotIn('--update', args)

    def test_validate_requires_business_note(self):
        self.engine.state['pending'] = {'version': 13}
        with self.assertRaises(ValueError):
            self.engine.validate({'ack_business': True, 'validation_note': ''})

    def test_successful_validation_promotes_matching_database_and_filestore(self):
        data = Path(self.tmp.name) / 'run-data'
        (data / 'filestore' / 'migration' / 'ab').mkdir(parents=True)
        (data / 'filestore' / 'migration' / 'ab' / 'attachment').write_bytes(b'file')
        self.engine.state.update(pending={'version': 13, 'before': {'contacts': '10'},
            'work': {'prefix': 'omig-test', 'db': 'omig-test-db', 'data_dir': str(data)},
            'decisions': {}, 'commits': {}, 'image': 'sha256:pinned'}, validated=False)
        def sql(container, db, query):
            return 'ab/attachment' if 'store_fname' in query else ''
        def dump(container, db, target):
            Path(target).write_bytes(b'PGDMP-test')
        with patch.object(self.engine, 'inspect', side_effect=RuntimeError('no preview')), \
             patch.object(self.engine, 'run', return_value=''), \
             patch.object(self.engine, 'sql', side_effect=sql), \
             patch.object(self.engine, 'dump', side_effect=dump), \
             patch.object(self.engine, 'metrics', return_value={'contacts': '10'}), \
             patch.object(self.engine, 'modules', return_value=[{'name': 'base', 'state': 'installed', 'latest_version': '13.0'}]):
            self.engine.validate({'ack_business': True, 'validation_note': 'Factures et pièces jointes vérifiées.'})
        self.assertEqual(self.engine.state['version'], 13)
        self.assertTrue(self.engine.state['validated'])
        self.assertIsNone(self.engine.state['pending'])
        self.assertTrue(Path(self.engine.state['checkpoint']).is_file())
        self.assertEqual((Path(self.engine.state['data']) / 'filestore/migration/ab/attachment').read_bytes(), b'file')

    def test_missing_attachment_blocks_checkpoint_promotion(self):
        self.engine.state.update(pending={'version': 13, 'work': {'prefix': 'omig-test',
            'db': 'omig-test-db', 'data_dir': self.tmp.name}}, validated=False)
        with patch.object(self.engine, 'inspect', side_effect=RuntimeError('no preview')), \
             patch.object(self.engine, 'run', return_value=''), \
             patch.object(self.engine, 'sql', return_value='missing/file'):
            with self.assertRaisesRegex(RuntimeError, 'pièces jointes'):
                self.engine.validate({'ack_business': True, 'validation_note': 'Factures et pièces jointes vérifiées.'})
        self.assertEqual(self.engine.state['version'], 12)
        self.assertFalse(self.engine.state['validated'])

    def test_retry_keeps_previous_checkpoint(self):
        self.engine.state.update(pending={'work': {'prefix': 'omig-test'}}, validated=False,
                                 checkpoint='previous.dump', version=12)
        with patch.object(self.engine, 'inspect', side_effect=RuntimeError('not found')):
            self.engine.retry({})
        self.assertEqual(self.engine.state['checkpoint'], 'previous.dump')
        self.assertEqual(self.engine.state['version'], 12)
        self.assertTrue(self.engine.state['validated'])
        self.assertIsNone(self.engine.state['pending'])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.proc = subprocess.Popen(['python3', str(Path(__file__).parent / 'migration.py'),
                                    '--workdir', cls.tmp.name, '--port', str(cls.port)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cls.url = cls.proc.stdout.readline().strip().split('Assistant : ', 1)[1]
        cls.token = cls.url.split('#')[1]
        cls.base = 'http://127.0.0.1:' + str(cls.port)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base, timeout=1).close()
                break
            except OSError:
                time.sleep(.02)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.communicate(timeout=5)
        cls.tmp.cleanup()

    def test_ui_and_authenticated_state(self):
        with urllib.request.urlopen(self.base) as response:
            self.assertIn('Assistant migration Odoo', response.read().decode())
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        req = urllib.request.Request(self.base + '/api/state', headers={'X-Session': self.token})
        with urllib.request.urlopen(req) as response:
            self.assertEqual(json.load(response)['state']['version'], 12)

    def test_state_without_token_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + '/api/state')
        self.assertEqual(error.exception.code, 403)

    def test_foreign_origin_denied(self):
        req = urllib.request.Request(self.base + '/api/audit', data=b'{}',
            headers={'X-Session': self.token, 'Origin': 'https://evil.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)

    def test_dns_rebinding_host_denied(self):
        req = urllib.request.Request(self.base + '/api/state', headers={'X-Session': self.token, 'Host': 'evil.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
