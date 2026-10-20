import ast
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src')]
import mail_online as mail
from g1_backup import BackupError

class OnlineMailTests(unittest.TestCase):
    def test_nonempty_or_malformed_queue_prevents_collection(self):
        for payload in (b'{"queue_id":"synthetic"}\n',b'unknown status\n'):
            with patch.object(mail,'command',return_value=payload):
                with self.assertRaises(BackupError):mail.queue_empty('synthetic-smtp')

    def test_empty_queue_allowed(self):
        with patch.object(mail,'command',return_value=b''):mail.queue_empty('synthetic-smtp')

    def test_wildcard_path_or_duplicate_users_rejected(self):
        for payload in (b'../outside\n',b'*@fixture.invalid\n',b'fixture@invalid\nfixture@invalid\n',b''):
            with patch.object(mail,'command',return_value=payload):
                with self.assertRaises(BackupError):mail.mailbox_users('synthetic-imap')

    def test_shared_namespace_is_rejected(self):
        with patch.object(mail,'command',side_effect=[b'maildir:/mail/%u',b'/mail/%u',b'namespace inbox {\n}\nnamespace shared {\n}\n']):
            with self.assertRaisesRegex(BackupError,'namespace'):mail.validate_imap('synthetic-imap')

    def test_cleanup_never_accepts_mail_or_other_temporary_paths(self):
        with patch.object(mail,'command') as execute:
            for path in ('/mail','/tmp','/tmp/g1-mail-online-x/../other','/tmp/other'):
                with self.assertRaises(BackupError):mail.cleanup('synthetic-imap',path,'8:12')
            execute.assert_not_called()

    def test_mail_sqlite_exports_committed_data_and_passes_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.db';dest=Path(td)/'private/export.db'
            c=sqlite3.connect(source);c.execute('CREATE TABLE fixture (value TEXT)')
            c.execute('INSERT INTO fixture VALUES (?)',('synthetic-value',));c.commit();c.close()
            mail.sqlite_export(source,dest)
            d=sqlite3.connect(dest)
            self.assertEqual(d.execute('SELECT value FROM fixture').fetchall(),[('synthetic-value',)])
            self.assertEqual(d.execute('PRAGMA integrity_check').fetchone(),('ok',));d.close()
            with self.assertRaises(BackupError):mail.sqlite_export(source,dest)

    def test_no_service_control_commands_or_reverse_sync_in_collectors(self):
        # Check real calls/command literals, not words in comments or a dummy mirror.
        for filename in ('prepare.py','mail_online.py','daily.py','first_backup.py'):
            tree=ast.parse((ROOT/'scripts'/filename).read_text())
            for node in ast.walk(tree):
                if not isinstance(node,ast.List) or not all(isinstance(x,ast.Constant) for x in node.elts[:2]):continue
                values=[x.value for x in node.elts if isinstance(x,ast.Constant)]
                self.assertNotIn(values[:2],[['docker','stop'],['docker','start'],['docker','restart'],['systemctl','stop']])
        self.assertNotIn("'sync'",mail.WORKER)
        self.assertNotIn("'-R'",mail.WORKER)
        compile(mail.WORKER,'container-worker','exec')

    def test_worker_stdout_and_command_errors_do_not_disclose_private_output(self):
        from types import SimpleNamespace
        with patch.object(mail.subprocess,'run',return_value=SimpleNamespace(returncode=13,stdout=b'private fixture',stderr=b'private fixture')):
            with self.assertRaises(BackupError) as error:mail.command(['docker','exec'])
            self.assertNotIn('private fixture',str(error.exception))

if __name__=='__main__':unittest.main()
