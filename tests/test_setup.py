import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('setup_router', Path(__file__).resolve().parents[1] / 'scripts/setup_router.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupSafetyTests(unittest.TestCase):
    def test_nonroot_has_no_system_side_effect(self):
        with patch.object(setup.os, 'geteuid', return_value=1000), patch.object(setup.subprocess, 'run') as run:
            with self.assertRaises(setup.BackupError):
                setup.apply()
            run.assert_not_called()

    def test_noninteractive_root_does_not_request_password(self):
        with patch.object(setup.os, 'geteuid', return_value=0), patch.object(setup.sys.stdin, 'isatty', return_value=False), patch.object(setup.getpass, 'getpass') as password:
            with self.assertRaises(setup.BackupError):
                setup.apply()
            password.assert_not_called()

    def test_never_overwrite_existing_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'credentials'
            p.write_text('synthetic-original')
            with self.assertRaises(FileExistsError):
                setup.new_file(p, 'replacement')
            self.assertEqual(p.read_text(), 'synthetic-original')

    def test_never_follow_credential_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'target'
            target.write_text('synthetic-original')
            link = Path(td) / 'link'
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                setup.new_file(link, 'replacement')
            self.assertEqual(target.read_text(), 'synthetic-original')

    def test_existing_nonempty_repository_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / 'old-backup').write_text('synthetic-original')
            with self.assertRaises(setup.BackupError):
                setup.existing_or_empty_repository(p)
            self.assertEqual((p / 'old-backup').read_text(), 'synthetic-original')
            self.assertEqual(len(list(p.iterdir())), 1)

    def test_budget_cannot_consume_shared_reserve(self):
        with patch('builtins.input', return_value='50'), patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.BackupError):
                setup.prompt_budget(50 * 1024 ** 3)

    def test_budget_has_no_silent_default(self):
        with patch('builtins.input', return_value=''), patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.BackupError):
                setup.prompt_budget(900 * 1024 ** 3)


class CredentialRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'smb-credentials'
        self.path.write_text('synthetic-original')
        self.path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_failed_reentry_preserves_old_credentials_and_cleans_candidates(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup.getpass, 'getpass', return_value='synthetic-new'), patch.object(setup, 'mount_with_credentials', side_effect=setup.MountFailure(32, 13)), patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.BackupError):
                setup.connect_share(self.path, reset_password=True)
        self.assertEqual(self.path.read_text(), 'synthetic-original')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_retry_success_replaces_only_after_connected_and_keeps_mode(self):
        observations = []

        def attempt(candidate, **kwargs):
            observations.append(self.path.read_text())
            if len(observations) == 1:
                raise setup.MountFailure(32, 13)

        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup.getpass, 'getpass', return_value='synthetic-new'), patch.object(setup, 'mount_with_credentials', side_effect=attempt), patch('sys.stdout', new_callable=io.StringIO) as output:
            setup.connect_share(self.path, reset_password=True)
        self.assertEqual(observations, ['synthetic-original', 'synthetic-original'])
        self.assertEqual(self.path.read_text(), 'username=backup-user\npassword=synthetic-new\n')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('synthetic-new', output.getvalue())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_saved_auth_failure_prompts_without_special_flag(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup.getpass, 'getpass', return_value='synthetic-new') as password, patch.object(setup, 'mount_with_credentials', side_effect=[setup.MountFailure(32, 13), None]), patch('sys.stdout', new_callable=io.StringIO):
            setup.connect_share(self.path)
        self.assertEqual(password.call_count, 2)
        self.assertIn('password=synthetic-new', self.path.read_text())

    def test_mismatched_confirmation_does_not_attempt_mount_or_replace(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup.getpass, 'getpass', side_effect=['synthetic-a', 'synthetic-b'] * 3), patch.object(setup, 'mount_with_credentials') as mount, patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.BackupError):
                setup.connect_share(self.path, reset_password=True)
            mount.assert_not_called()
        self.assertEqual(self.path.read_text(), 'synthetic-original')

    def test_cancel_preserves_credentials(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup.getpass, 'getpass', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                setup.connect_share(self.path, reset_password=True)
        self.assertEqual(self.path.read_text(), 'synthetic-original')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_connected_share_is_not_disconnected_for_reset(self):
        with patch.object(setup.os.path, 'ismount', return_value=True), patch.object(setup, 'check_mount'), patch.object(setup.getpass, 'getpass') as password, patch.object(setup.subprocess, 'run') as command:
            with self.assertRaises(setup.BackupError):
                setup.connect_share(self.path, reset_password=True)
            password.assert_not_called()
            command.assert_not_called()

    def test_mount_failure_reports_only_numbers_not_raw_output(self):
        result = Mock(returncode=32, stderr=b'mount error(13): synthetic-sensitive-output')
        with patch.object(setup.subprocess, 'run', return_value=result):
            with self.assertRaises(setup.BackupError) as caught:
                setup.mount_with_credentials(self.path)
        self.assertIn('13', str(caught.exception))
        self.assertNotIn('synthetic-sensitive-output', str(caught.exception))

    def test_invalid_argument_does_not_request_password_for_saved_auth(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup, 'mount_with_credentials', side_effect=setup.MountFailure(32, 22)), patch.object(setup.getpass, 'getpass') as password, patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.MountFailure):
                setup.connect_share(self.path)
            password.assert_not_called()
        self.assertEqual(self.path.read_text(), 'synthetic-original')

    def test_invalid_argument_stops_after_one_input_pair(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup, 'mount_with_credentials', side_effect=setup.MountFailure(32, 22)) as mount, patch.object(setup.getpass, 'getpass', return_value='synthetic-new') as password, patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.MountFailure):
                setup.connect_share(self.path, reset_password=True)
            self.assertEqual(password.call_count, 2)
            self.assertEqual(mount.call_count, 1)
        self.assertEqual(self.path.read_text(), 'synthetic-original')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_network_error_does_not_request_password(self):
        with patch.object(setup.os.path, 'ismount', return_value=False), patch.object(setup, 'mount_with_credentials', side_effect=setup.MountFailure(32, 110)), patch.object(setup.getpass, 'getpass') as password, patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(setup.MountFailure):
                setup.connect_share(self.path)
            password.assert_not_called()

    def test_mount_requests_smb20_matching_router(self):
        with patch.object(setup.subprocess, 'run', return_value=Mock(returncode=0)) as command, patch.object(setup, 'check_mount'):
            setup.mount_with_credentials(self.path)
        options = command.call_args.args[0][-1]
        self.assertIn('vers=2.0', options)
        self.assertNotIn('synthetic-original', options)

    def test_smb1_is_not_an_allowed_mount_version(self):
        with patch.object(setup.subprocess, 'run') as command:
            with self.assertRaises(setup.BackupError):
                setup.mount_with_credentials(self.path, smb_version='1.0')
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
