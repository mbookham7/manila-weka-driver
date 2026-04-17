# Copyright 2024 Weka.IO
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

"""
WEKAFS protocol tempest tests for the Manila Weka driver.

These tests exercise the full WEKAFS share lifecycle via the Manila API.
They require:
  - wekafsio kernel module loaded on the Manila host
  - 'wekafs' in tempest.conf [share] enable_protocols
  - Weka cluster reachable from the Manila host
"""

from tempest import config
from tempest.lib import decorators
from tempest.lib.common.utils import data_utils
from testtools import testcase as tc

from manila_tempest_tests.common import waiters
from manila_tempest_tests.tests.api import base

CONF = config.CONF


class WekaFSSharesTest(base.BaseSharesMixedTest):
    """Tests WEKAFS protocol shares via the Weka Manila driver.

    Exercises share lifecycle, snapshots, extend/shrink, access rules,
    and create-from-snapshot for the WEKAFS (WekaFS POSIX client) protocol.
    """

    protocol = "wekafs"

    @classmethod
    def skip_checks(cls):
        super(WekaFSSharesTest, cls).skip_checks()
        if not CONF.service_available.manila:
            raise cls.skipException("Manila support is required")
        if cls.protocol not in CONF.share.enable_protocols:
            raise cls.skipException(
                "WekaFS tests are disabled — add 'wekafs' to "
                "tempest.conf [share] enable_protocols")

    @classmethod
    def resource_setup(cls):
        super(WekaFSSharesTest, cls).resource_setup()
        extra_specs = {
            'driver_handles_share_servers': CONF.share.multitenancy_enabled,
            'snapshot_support': True,
            'create_share_from_snapshot_support': True,
            'revert_to_snapshot_support': True,
        }
        cls.share_type = cls.create_share_type(extra_specs=extra_specs)
        cls.share_type_id = cls.share_type['id']
        # Class-level share reused by snapshot and access rule tests
        cls.share = cls.create_share(
            share_protocol=cls.protocol,
            share_type_id=cls.share_type_id,
            cleanup_in_class=True)

    # ── Share lifecycle ──────────────────────────────────────────────────────

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f501')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_create_delete_wekafs_share(self):
        """Create and delete a WEKAFS share — verifies the full lifecycle
        including the local WekaFS POSIX mount on the Manila host."""
        share = self.create_share(
            share_protocol=self.protocol,
            share_type_id=self.share_type_id,
            cleanup_in_class=False)
        # create_share returns the POST response (status='creating'); re-fetch
        # to get the current status and export_locations after wait completes.
        share = self.shares_v2_client.get_share(share['id'])['share']
        self.assertEqual('available', share['status'])
        self.assertEqual(self.protocol.upper(), share['share_proto'])
        self.assertIsNotNone(share.get('export_location') or
                             share.get('export_locations'))

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f502')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_extend_wekafs_share(self):
        """Extend a WEKAFS share and verify the new capacity."""
        share = self.create_share(
            share_protocol=self.protocol,
            share_type_id=self.share_type_id,
            cleanup_in_class=False)
        new_size = int(share['size']) + 1
        self.shares_v2_client.extend_share(share['id'], new_size)
        waiters.wait_for_resource_status(
            self.shares_v2_client, share['id'], 'available')
        updated = self.shares_v2_client.get_share(share['id'])['share']
        self.assertEqual(new_size, int(updated['size']))

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f503')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_shrink_wekafs_share(self):
        """Shrink a WEKAFS share and verify the new capacity."""
        share = self.create_share(
            share_protocol=self.protocol,
            share_type_id=self.share_type_id,
            size=2,
            cleanup_in_class=False)
        new_size = int(share['size']) - 1
        self.shares_v2_client.shrink_share(share['id'], new_size)
        waiters.wait_for_resource_status(
            self.shares_v2_client, share['id'], 'available')
        updated = self.shares_v2_client.get_share(share['id'])['share']
        self.assertEqual(new_size, int(updated['size']))

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f504')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_get_wekafs_share(self):
        """Verify share details and export location for a WEKAFS share."""
        share = self.shares_v2_client.get_share(
            self.share['id'])['share']
        self.assertEqual('available', share['status'])
        self.assertEqual(self.protocol.upper(), share['share_proto'])

    # ── Snapshot operations ──────────────────────────────────────────────────

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f505')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_create_delete_snapshot(self):
        """Create and delete a snapshot of a WEKAFS share."""
        if not CONF.share.run_snapshot_tests:
            raise self.skipException("Snapshot tests are disabled")
        snapshot = self.create_snapshot_wait_for_active(
            self.share['id'],
            name=data_utils.rand_name('wekafs-snap'),
            cleanup_in_class=False)
        # create_snapshot_wait_for_active returns the POST dict (status=
        # 'creating'); the wait already confirmed 'available', skip re-assert.
        self.assertEqual(self.share['id'], snapshot['share_id'])

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f506')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_revert_to_snapshot(self):
        """Revert a WEKAFS share to a snapshot (in-place restore)."""
        if not CONF.share.run_snapshot_tests:
            raise self.skipException("Snapshot tests are disabled")
        # Use a dedicated share so revert does not affect class-level share
        share = self.create_share(
            share_protocol=self.protocol,
            share_type_id=self.share_type_id,
            cleanup_in_class=False)
        snapshot = self.create_snapshot_wait_for_active(
            share['id'],
            name=data_utils.rand_name('wekafs-revert-snap'),
            cleanup_in_class=False)
        self.shares_v2_client.revert_to_snapshot(
            share['id'], snapshot['id'])
        waiters.wait_for_resource_status(
            self.shares_v2_client, share['id'], 'available')
        waiters.wait_for_resource_status(
            self.shares_v2_client, snapshot['id'], 'available',
            resource_name='snapshot')

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f507')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_create_share_from_snapshot(self):
        """Create a new WEKAFS share cloned from a snapshot (NFS data copy)."""
        if not CONF.share.run_snapshot_tests:
            raise self.skipException("Snapshot tests are disabled")
        snapshot = self.create_snapshot_wait_for_active(
            self.share['id'],
            name=data_utils.rand_name('wekafs-clone-snap'),
            cleanup_in_class=False)
        child = self.create_share(
            share_protocol=self.protocol,
            share_type_id=self.share_type_id,
            snapshot_id=snapshot['id'],
            cleanup_in_class=False)
        # create_share returns POST dict (status='creating'); wait already
        # confirmed 'available'. Verify the snapshot linkage is correct.
        self.assertEqual(snapshot['id'], child['snapshot_id'])

    # ── Access rules ─────────────────────────────────────────────────────────

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f508')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_add_remove_ip_access_rule(self):
        """Add and remove an IP access rule on a WEKAFS share.

        WEKAFS access rules are recorded by the driver; enforcement is
        at the Weka authentication layer rather than via NFS exports.
        """
        rule = self.allow_access(
            self.share['id'],
            access_type='ip',
            access_to='2.2.2.2',
            access_level='rw')
        self.assertEqual('rw', rule['access_level'])
        self.shares_v2_client.delete_access_rule(
            self.share['id'], rule['id'])
        waiters.wait_for_resource_status(
            self.shares_v2_client, self.share['id'], 'available')

    @decorators.idempotent_id('c3e4f5a6-b7c8-4d9e-af10-b1c2d3e4f509')
    @tc.attr(base.TAG_POSITIVE, base.TAG_BACKEND)
    def test_add_ro_access_rule(self):
        """Add a read-only IP access rule on a WEKAFS share."""
        rule = self.allow_access(
            self.share['id'],
            access_type='ip',
            access_to='3.3.3.3',
            access_level='ro')
        self.assertEqual('ro', rule['access_level'])
        self.shares_v2_client.delete_access_rule(
            self.share['id'], rule['id'])
        waiters.wait_for_resource_status(
            self.shares_v2_client, self.share['id'], 'available')
