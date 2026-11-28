"""PERIPH-0.2: entirely fake systemd/devices, no host or robot actions."""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import stat
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE))
import host_broker as h
import control_cli as cli

SAMPLE_REQUEST = {
    'schema_version': h.SCHEMA,
    'request_id': 'f2508cc3-538f-4a41-bc4e-f979d1796f10',
    'robot_id': 'fixture-robot-a',
    'peripheral_id': 'fixture-imu-a',
    'action': 'status',
}
SERVICE = {
    'robot_id': 'fixture-robot-a',
    'peripheral_id': 'fixture-imu-a',
    'service_ref': 'openrgd-fixture-robot-a-imu.service',
    'device_by_path': '/dev/serial/by-path/fixture-only-a',
    'usb_vid_pid': '1a86:7523',
    'resource_lock': 'fixture-robot-a:imu_serial_exclusive',
}


class SystemdFake:
    def __init__(self, active='inactive', loaded='loaded'):
        self.active = active
        self.loaded = loaded
        self.calls = []
        self.enabled = False

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        assert isinstance(argv, list) and kw.get('check') is False
        assert 'shell' not in kw
        assert argv[0] in (h.SYSTEMCTL, h.UDEVADM)
        if argv[0] == h.UDEVADM:
            return SimpleNamespace(returncode=0,
                                   stdout='ID_VENDOR_ID=1a86\nID_MODEL_ID=7523\n',
                                   stderr='')
        assert argv[:2] == [h.SYSTEMCTL, '--user']
        assert argv[3] == 'openrgd-fixture-robot-a-imu.service'
        if argv[2] == 'show':
            return SimpleNamespace(returncode=0, stdout=(
                f'LoadState={self.loaded}\nActiveState={self.active}\n'
                f'UnitFileState={"enabled" if self.enabled else "disabled"}\nSubState=dead\n'
            ), stderr='')
        if argv[2] == 'start':
            self.active = 'active'
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        if argv[2] == 'stop':
            self.active = 'inactive'
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        raise AssertionError('Unexpected command')


def manager(fake=None) -> tuple[h.Broker, SystemdFake]:
    fake = fake or SystemdFake()
    return h.Broker({'schema_version': h.ALLOWLIST_SCHEMA, 'services': [SERVICE]}, fake), fake


def req(action='status', uuid=None, revision=None):
    row = dict(SAMPLE_REQUEST)
    row['action'] = action
    if uuid:
        row['request_id'] = uuid
    if revision is not None:
        row['expected_status_revision'] = revision
    return row


class TestPolicy(unittest.TestCase):
    def test_service_identity_and_allowlist_reject_untrusted_files(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'allowlist.json'
            path.write_text(json.dumps({'schema_version': h.ALLOWLIST_SCHEMA,
                                        'services': [SERVICE]}))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                h.read_allowlist(path)
            path.chmod(0o600)
            self.assertEqual(h.read_allowlist(path)['services'][0]['service_ref'],
                             SERVICE['service_ref'])
            bad = {'schema_version': h.ALLOWLIST_SCHEMA,
                   'services': [dict(SERVICE, service_ref='evil.service')]}
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                h.read_allowlist(path)
            bad['services'] = [dict(SERVICE), dict(SERVICE,
                                                    peripheral_id='another-imu')]
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                h.read_allowlist(path)
            path.unlink()
            path.symlink_to(Path(root) / 'elsewhere.json')
            with self.assertRaises(ValueError):
                h.read_allowlist(path)

    def test_publisher_started_does_not_mean_sensor_live(self):
        broker, fake = manager(SystemdFake(active='active'))
        with patch.object(h, 'device_identity', return_value=('present', True)):
            status = broker.process(req())
        self.assertEqual(status['publisher']['lifecycle'], 'running')
        self.assertEqual(status['device']['presence'], 'present')
        self.assertNotIn('streams', status)  # Console must join stream freshness.
        self.assertFalse(status['actuation_enabled'])
        self.assertTrue(all('systemctl' not in str(x) or x[0] == h.SYSTEMCTL
                            for x in fake.calls))

    def test_start_stop_idempotency_and_duplicate_request_ledger(self):
        broker, fake = manager()
        with patch.object(h, 'device_identity', return_value=('present', True)):
            initial = broker.process(req())
            start_id = 'a50c1501-798e-4a56-adf1-515469ac3444'
            command = req('acquisition.start', start_id,
                          initial['status_revision'])
            started = broker.process(command)
            self.assertEqual(started['disposition'], 'accepted')
            self.assertEqual(started['publisher']['lifecycle'], 'running')
            self.assertEqual(broker.process(command), started)
            self.assertEqual(fake.active, 'active')
            self.assertEqual(len([c for c in fake.calls if 'start' in c]), 1)

            wrong_action = dict(command, action='acquisition.stop')
            self.assertEqual(broker.process(wrong_action)['error_code'],
                             'request_id_reused')
            running = broker.process(req())
            new_start = req('acquisition.start',
                            'a50c1502-798e-4a56-adf1-515469ac3444',
                            running['status_revision'])
            self.assertEqual(broker.process(new_start)['disposition'], 'no_op')
            stopped = broker.process(req('acquisition.stop',
                                         'a50c1503-798e-4a56-adf1-515469ac3444',
                                         running['status_revision']))
            self.assertEqual(stopped['publisher']['lifecycle'], 'stopped')
            self.assertEqual(len([c for c in fake.calls if 'stop' in c]), 1)

    def test_stale_revision_unknown_identity_and_malicious_json_fail_closed(self):
        broker, fake = manager()
        with patch.object(h, 'device_identity', return_value=('present', True)):
            present = broker.process(req())
            self.assertEqual(broker.process(req('acquisition.start',
                revision=present['status_revision'] + 1))['error_code'],
                'stale_status_revision')
            for invalid in [
                {'schema_version': h.SCHEMA, 'robot_id': [], 'peripheral_id': 'fixture-imu-a',
                 'request_id': SAMPLE_REQUEST['request_id'], 'action': 'status'},
                dict(req(), peripheral_id=['fixture-imu-a']),
                dict(req(), action='motor.enable'),
                dict(req(), action='publisher.exec'),
                dict(req(), arbitrary_shell='rm -rf /'),
                dict(req(), request_id='invalid'),
                dict(req(), robot_id='unknown-robot'),
            ]:
                with self.subTest(invalid=invalid):
                    result = broker.process(invalid)
                    self.assertEqual(result['disposition'], 'rejected')
                    self.assertFalse(result['actuation_enabled'])
            self.assertFalse(any('start' in c or 'stop' in c for c in fake.calls))

    def test_service_missing_broker_does_not_guess_running_or_stop(self):
        broker, fake = manager(SystemdFake(active='inactive', loaded='not-found'))
        with patch.object(h, 'device_identity', return_value=('absent', False)):
            status = broker.process(req())
            self.assertEqual(status['publisher']['lifecycle'], 'not_installed')
            denied = broker.process(req('acquisition.start',
                                        revision=status['status_revision']))
            self.assertEqual(denied['error_code'], 'service_unavailable')
            self.assertEqual(denied['device']['presence'], 'absent')
        self.assertFalse(any(c[2] == 'start' for c in fake.calls))

    def test_device_not_present_or_unverified_disallows_start(self):
        for presence, verified in [('absent', False), ('present', False)]:
            broker, fake = manager()
            with patch.object(h, 'device_identity', return_value=(presence, verified)):
                status = broker.process(req())
                denied = broker.process(req('acquisition.start',
                                            revision=status['status_revision']))
                self.assertEqual(denied['error_code'], 'device_missing_or_unverified')
                self.assertEqual(fake.active, 'inactive')

    def test_only_fixed_systemd_unit_ever_operated_and_no_shell(self):
        fake = SystemdFake()
        self.assertFalse(h.run_unit('restart', 'openrgd-fixture-robot-a-imu.service', fake))
        self.assertFalse(h.run_unit('start', 'evil.service', fake))
        self.assertTrue(h.run_unit('start', 'openrgd-fixture-robot-a-imu.service', fake))
        self.assertEqual(fake.calls[0],
                         ['/usr/bin/systemctl', '--user', 'start',
                          'openrgd-fixture-robot-a-imu.service', '--no-pager'])

    def test_same_uid_socket_credential_check(self):
        if not hasattr(socket, 'SO_PEERCRED'):
            self.skipTest('Linux SO_PEERCRED only')
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with left, right:
            self.assertTrue(h.peer_allowed(left))
            self.assertFalse(h.peer_allowed(left, os.getuid() + 1))

    def test_json_protocol_over_real_private_socket_without_any_host_command(self):
        broker, fake = manager()
        with TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o700)
            path = root / 'openrgd' / 'peripherals.sock'
            stop = threading.Event()
            with patch.object(h, 'device_identity', return_value=('absent', False)):
                worker = threading.Thread(target=h.serve,
                    kwargs={'manager': broker, 'path': path,
                            'stop_event': stop, 'register_handlers': False},
                    daemon=True)
                worker.start()
                try:
                    for _ in range(30):
                        if path.exists():
                            break
                        time.sleep(0.02)
                    self.assertTrue(path.exists())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                    status = cli.transact(path, req())
                    self.assertEqual(status['disposition'], 'status')
                    self.assertEqual(status['device']['presence'], 'absent')
                    self.assertEqual(status['publisher']['lifecycle'], 'stopped')
                finally:
                    stop.set()
                    worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(path.exists())
        self.assertEqual(fake.active, 'inactive')

    def test_two_robot_registry_scope_is_independent(self):
        second = dict(SERVICE, robot_id='fixture-robot-b',
                      peripheral_id='fixture-imu-b',
                      service_ref='openrgd-fixture-robot-b-imu.service',
                      device_by_path='/dev/serial/by-path/fixture-only-b',
                      resource_lock='fixture-robot-b:imu_serial_exclusive')
        fake = SystemdFake()
        broker = h.Broker({'schema_version': h.ALLOWLIST_SCHEMA,
                           'services': [SERVICE, second]}, fake)
        with patch.object(h, 'systemd_show', return_value=('stopped', False, True)), \
             patch.object(h, 'device_identity', return_value=('present', True)):
            first = broker.process(req())
            other = broker.process(dict(req(), robot_id='fixture-robot-b',
                                        peripheral_id='fixture-imu-b'))
            self.assertEqual(first['publisher']['service_ref'], SERVICE['service_ref'])
            self.assertEqual(other['publisher']['service_ref'], second['service_ref'])
            mixed = broker.process(dict(req(), robot_id='fixture-robot-b'))
            self.assertEqual(mixed['error_code'], 'unregistered_peripheral')
        self.assertEqual(fake.calls, [])

    def test_cli_requires_explicit_robot_and_peripheral(self):
        with patch('sys.stderr'):
            with self.assertRaises(SystemExit) as error:
                cli.main(['status'])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
