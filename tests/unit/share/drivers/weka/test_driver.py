# Copyright 2024 Weka.IO Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Unit tests for manila.share.drivers.weka.driver."""

import unittest
from unittest import mock

from oslo_config import cfg

from manila.common import constants
from manila import exception
from manila.share.drivers.weka import driver as weka_driver
from manila.share.drivers.weka import exceptions as weka_exc
from manila.share.drivers.weka import posix as weka_posix
from tests.unit.share.drivers.weka import fakes

CONF = cfg.CONF


def _make_config(**kwargs):
    """Return a mock configuration object."""
    defaults = {
        'weka_api_server': 'weka-test.example.com',
        'weka_api_port': 14000,
        'weka_username': 'admin',
        'weka_password': 'secret',
        'weka_organization': 'Root',
        'weka_ssl_verify': False,
        'weka_filesystem_group': 'default',
        'weka_mount_point_base': '/mnt/weka',
        'weka_num_cores': 1,
        'weka_net_device': None,
        'weka_posix_mount_timeout': 60,
        'weka_api_timeout': 30,
        'weka_max_api_retries': 3,
        'weka_share_name_prefix': 'manila_',
        'share_backend_name': 'weka',
    }
    defaults.update(kwargs)

    config = mock.Mock()
    config.safe_get = lambda key: defaults.get(key)
    return config


class TestWekaShareDriverSetup(unittest.TestCase):

    def _make_driver(self, **cfg_kwargs):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config(**cfg_kwargs)
        drv._client = None
        drv._fs_group_uid = None
        return drv

    @mock.patch('manila.share.drivers.weka.client.WekaApiClient')
    def test_do_setup_creates_client_and_logs(self, mock_client_cls):
        drv = self._make_driver()
        mock_client = mock.Mock()
        mock_client.get_cluster_status.return_value = (
            fakes.fake_cluster_status())
        mock_client.get_filesystem_group_by_name.return_value = (
            fakes.fake_filesystem_group())
        mock_client_cls.return_value = mock_client

        drv.do_setup(context=None)

        mock_client.login.assert_called_once()
        self.assertIsNotNone(drv._client)

    @mock.patch('manila.share.drivers.weka.client.WekaApiClient')
    def test_do_setup_creates_fs_group_if_missing(self, mock_client_cls):
        drv = self._make_driver()
        mock_client = mock.Mock()
        mock_client.get_cluster_status.return_value = (
            fakes.fake_cluster_status())
        mock_client.get_filesystem_group_by_name.return_value = None
        mock_client.create_filesystem_group.return_value = (
            fakes.fake_filesystem_group())
        mock_client_cls.return_value = mock_client

        drv.do_setup(context=None)

        mock_client.create_filesystem_group.assert_called_once_with('default')

    def test_check_for_setup_error_missing_required(self):
        drv = self._make_driver(weka_api_server=None)
        self.assertRaises(
            exception.InvalidInput, drv.check_for_setup_error)

    @mock.patch('builtins.open',
                mock.mock_open(read_data='nodev wekafs\n'))
    def test_check_for_setup_error_wekafs_loaded(self):
        drv = self._make_driver()
        drv._client = mock.Mock()
        drv._client.get_cluster_status.return_value = {}
        # Should not raise
        drv.check_for_setup_error()

    def test_check_for_setup_error_auth_failure(self):
        drv = self._make_driver()
        drv._client = mock.Mock()
        drv._client.get_cluster_status.side_effect = (
            weka_exc.WekaAuthError(reason='bad creds'))
        with mock.patch('builtins.open',
                        mock.mock_open(read_data='nodev wekafs\n')):
            self.assertRaises(
                exception.ManilaException, drv.check_for_setup_error)


class TestWekaShareDriverCreateShare(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_create_share_wekafs(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None
        drv._client.create_filesystem.return_value = fakes.fake_filesystem()

        share = fakes.fake_share(proto='WEKAFS')
        result = drv.create_share(context=None, share=share)

        drv._client.create_filesystem.assert_called_once()
        self.assertEqual(1, len(result))
        path = result[0]['path']
        self.assertIn(fakes.FAKE_FS_NAME, path)

    def test_create_share_nfs(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None
        drv._client.create_filesystem.return_value = fakes.fake_filesystem()

        share = fakes.fake_share(proto='NFS')
        result = drv.create_share(context=None, share=share)

        self.assertEqual(1, len(result))
        self.assertIn(':/', result[0]['path'])

    def test_create_share_unsupported_protocol(self):
        drv = self._make_driver()
        share = fakes.fake_share(proto='CEPHFS')
        self.assertRaises(
            exception.InvalidShare,
            drv.create_share, None, share)

    def test_create_share_idempotent_when_fs_exists(self):
        drv = self._make_driver()
        existing_fs = fakes.fake_filesystem()
        drv._client.get_filesystem_by_name.return_value = existing_fs

        share = fakes.fake_share(proto='WEKAFS')
        result = drv.create_share(context=None, share=share)

        drv._client.create_filesystem.assert_not_called()
        self.assertEqual(1, len(result))

    def test_create_share_stores_fs_uid_in_metadata(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None
        drv._client.create_filesystem.return_value = fakes.fake_filesystem()

        share = fakes.fake_share(proto='WEKAFS')
        result = drv.create_share(context=None, share=share)

        meta = result[0].get('metadata', {})
        self.assertEqual(fakes.FAKE_FS_UID, meta.get('weka_fs_uid'))


class TestWekaShareDriverDeleteShare(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_delete_share(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        drv._client.list_nfs_permissions.return_value = []

        with mock.patch.object(weka_posix.WekaMount, 'is_mounted',
                               return_value=False):
            drv.delete_share(context=None, share=fakes.fake_share())

        drv._client.delete_filesystem.assert_called_once_with(
            fakes.FAKE_FS_UID)

    def test_delete_share_idempotent_when_not_found(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None

        # Should not raise
        drv.delete_share(context=None, share=fakes.fake_share())
        drv._client.delete_filesystem.assert_not_called()

    def test_delete_share_removes_nfs_permissions(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        perm = fakes.fake_nfs_permission()
        drv._client.list_nfs_permissions.return_value = [perm]

        with mock.patch.object(weka_posix.WekaMount, 'is_mounted',
                               return_value=False):
            drv.delete_share(context=None, share=fakes.fake_share())

        drv._client.delete_nfs_permission.assert_called_once_with(
            fakes.FAKE_PERM_UID)


class TestWekaShareDriverExtendShrink(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_extend_share(self):
        drv = self._make_driver()
        share = fakes.fake_share(size=10)
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())

        drv.extend_share(share, new_size=20)

        drv._client.update_filesystem.assert_called_once_with(
            fakes.FAKE_FS_UID,
            total_capacity=20 * 1024 ** 3,
        )

    def test_shrink_share_success(self):
        drv = self._make_driver()
        share = fakes.fake_share(size=10)
        # used = 1 GiB, shrinking to 5 GiB — OK
        fs = fakes.fake_filesystem(
            total_capacity=10 * 1024 ** 3,
            used_size_bytes=1 * 1024 ** 3,
        )
        drv._client.get_filesystem_by_name.return_value = fs
        drv._client.get_filesystem.return_value = fs

        drv.shrink_share(share, new_size=5)

        drv._client.update_filesystem.assert_called_once_with(
            fakes.FAKE_FS_UID,
            total_capacity=5 * 1024 ** 3,
        )

    def test_shrink_share_raises_when_used_gt_new_size(self):
        drv = self._make_driver()
        share = fakes.fake_share(size=10)
        # used = 8 GiB, trying to shrink to 5 GiB
        fs = fakes.fake_filesystem(
            total_capacity=10 * 1024 ** 3,
            used_size_bytes=8 * 1024 ** 3,
        )
        drv._client.get_filesystem_by_name.return_value = fs
        drv._client.get_filesystem.return_value = fs

        self.assertRaises(
            exception.ShareShrinkingPossibleDataLoss,
            drv.shrink_share, share, new_size=5,
        )


class TestWekaShareDriverSnapshots(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_create_snapshot(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        snap_model = fakes.fake_snapshot_model()

        drv.create_snapshot(context=None, snapshot=snap_model)

        drv._client.create_snapshot.assert_called_once()

    def test_delete_snapshot(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        snap = fakes.fake_snapshot()
        drv._client.get_snapshot_by_name.return_value = snap
        snap_model = fakes.fake_snapshot_model()

        drv.delete_snapshot(context=None, snapshot=snap_model)

        drv._client.delete_snapshot.assert_called_once_with(
            fakes.FAKE_SNAP_UID)

    def test_delete_snapshot_idempotent_when_not_found(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        drv._client.get_snapshot_by_name.return_value = None
        snap_model = fakes.fake_snapshot_model()

        # Should not raise
        drv.delete_snapshot(context=None, snapshot=snap_model)
        drv._client.delete_snapshot.assert_not_called()

    def test_delete_snapshot_idempotent_when_share_not_found(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None
        snap_model = fakes.fake_snapshot_model()

        # Should not raise
        drv.delete_snapshot(context=None, snapshot=snap_model)
        drv._client.delete_snapshot.assert_not_called()

    def test_revert_to_snapshot(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        snap = fakes.fake_snapshot()
        drv._client.get_snapshot_by_name.return_value = snap
        snap_model = fakes.fake_snapshot_model()

        drv.revert_to_snapshot(
            context=None, snapshot=snap_model,
            share_access_rules=[], snapshot_access_rules=[])

        drv._client.restore_snapshot.assert_called_once_with(
            fakes.FAKE_SNAP_UID)

    def test_revert_to_snapshot_raises_when_not_found(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        drv._client.get_snapshot_by_name.return_value = None
        snap_model = fakes.fake_snapshot_model()

        self.assertRaises(
            exception.SnapshotNotFound,
            drv.revert_to_snapshot,
            None, snap_model, [], [],
        )


class TestWekaShareDriverUpdateAccess(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_update_access_nfs_add_ip_rule(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        drv._client.create_client_group.return_value = (
            fakes.fake_client_group())
        drv._client.add_client_group_rule.return_value = {}
        drv._client.create_nfs_permission.return_value = (
            fakes.fake_nfs_permission())

        share = fakes.fake_share(proto='NFS')
        rule = fakes.fake_access_rule(access_type='ip',
                                      access_to='192.168.1.0/24')
        drv.update_access(
            context=None, share=share,
            access_rules=[], add_rules=[rule], delete_rules=[],
            update_rules=[],
        )

        drv._client.create_client_group.assert_called_once()
        drv._client.create_nfs_permission.assert_called_once()

    def test_update_access_nfs_full_sync(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())
        drv._client.create_client_group.return_value = (
            fakes.fake_client_group())
        drv._client.add_client_group_rule.return_value = {}
        drv._client.create_nfs_permission.return_value = (
            fakes.fake_nfs_permission())

        share = fakes.fake_share(proto='NFS')
        rule = fakes.fake_access_rule(access_type='ip',
                                      access_to='10.0.0.0/24')
        # Full sync: access_rules populated, add/delete empty
        drv.update_access(
            context=None, share=share,
            access_rules=[rule], add_rules=[], delete_rules=[],
            update_rules=[],
        )

        drv._client.create_nfs_permission.assert_called_once()

    def test_update_access_nfs_invalid_type_sets_error(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = (
            fakes.fake_filesystem())

        share = fakes.fake_share(proto='NFS')
        rule = fakes.fake_access_rule(access_type='user',
                                      access_to='bob')
        rule_id = rule['access_id']
        result = drv.update_access(
            context=None, share=share,
            access_rules=[], add_rules=[rule], delete_rules=[],
            update_rules=[],
        )

        self.assertIn(rule_id, result)
        self.assertEqual('error', result[rule_id]['state'])

    def test_update_access_wekafs_unsupported_type_sets_error(self):
        drv = self._make_driver()
        share = fakes.fake_share(proto='WEKAFS')
        rule = fakes.fake_access_rule(access_type='cert',
                                      access_to='my-cert')
        result = drv.update_access(
            context=None, share=share,
            access_rules=[], add_rules=[rule], delete_rules=[],
            update_rules=[],
        )
        self.assertIn(rule['access_id'], result)


class TestWekaShareDriverStats(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        # Provide a stub for _update_share_stats super call
        drv._stats = {}
        return drv

    def test_update_share_stats_fields(self):
        drv = self._make_driver()
        cap = fakes.fake_capacity(
            total_bytes=100 * 1024 ** 3,
            used_bytes=30 * 1024 ** 3,
        )
        drv._client.get_capacity.return_value = cap

        with mock.patch.object(
                weka_driver.driver.ShareDriver, '_update_share_stats'):
            drv._update_share_stats()

    def test_update_share_stats_handles_api_error(self):
        drv = self._make_driver()
        drv._client.get_capacity.side_effect = Exception("API down")

        with mock.patch.object(
                weka_driver.driver.ShareDriver, '_update_share_stats'):
            # Should not raise; falls back to zeros.
            drv._update_share_stats()


class TestWekaShareDriverManage(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_manage_existing_success(self):
        drv = self._make_driver()
        fs = fakes.fake_filesystem(total_capacity=20 * 1024 ** 3)
        drv._client.get_filesystem_by_name.return_value = fs

        share = fakes.fake_share()
        result = drv.manage_existing(share, driver_options={})

        self.assertIn('size', result)
        self.assertEqual(20, result['size'])

    def test_manage_existing_not_found(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None

        share = fakes.fake_share()
        self.assertRaises(
            exception.ManageInvalidShare,
            drv.manage_existing, share, {},
        )

    def test_unmanage_does_not_delete(self):
        drv = self._make_driver()
        drv.unmanage(share=fakes.fake_share())
        drv._client.delete_filesystem.assert_not_called()


class TestWekaShareDriverMiscellaneous(unittest.TestCase):

    def _make_driver(self):
        drv = weka_driver.WekaShareDriver.__new__(weka_driver.WekaShareDriver)
        drv.configuration = _make_config()
        drv._client = mock.Mock()
        drv._fs_group_uid = fakes.FAKE_GROUP_UID
        return drv

    def test_get_network_allocations_number(self):
        drv = self._make_driver()
        self.assertEqual(0, drv.get_network_allocations_number())

    def test_share_name_uses_prefix(self):
        drv = self._make_driver()
        name = drv._share_name('my-uuid')
        self.assertEqual('manila_my-uuid', name)

    def test_snapshot_name(self):
        drv = self._make_driver()
        name = drv._snapshot_name('snap-uuid')
        self.assertEqual('snap_snap-uuid', name)

    def test_mount_point(self):
        drv = self._make_driver()
        mp = drv._mount_point('manila_my-uuid')
        self.assertEqual('/mnt/weka/manila_my-uuid', mp)

    def test_ensure_share_re_mounts_if_not_mounted(self):
        drv = self._make_driver()
        fs = fakes.fake_filesystem()
        drv._client.get_filesystem_by_name.return_value = fs

        share = fakes.fake_share(proto='WEKAFS')

        with mock.patch.object(weka_posix.WekaMount, 'is_mounted',
                               return_value=False):
            with mock.patch.object(weka_posix.WekaMount, 'mount') as mock_mnt:
                drv.ensure_share(context=None, share=share)
        mock_mnt.assert_called_once()

    def test_ensure_share_not_found_raises(self):
        drv = self._make_driver()
        drv._client.get_filesystem_by_name.return_value = None
        share = fakes.fake_share()
        self.assertRaises(
            exception.ShareNotFound,
            drv.ensure_share, None, share,
        )


if __name__ == '__main__':
    unittest.main()
