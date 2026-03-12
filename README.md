# Manila Weka Driver

OpenStack Manila share driver for [Weka](https://www.weka.io/) storage,
using the WekaFS POSIX client for optimal performance.

## Overview

This driver exposes Weka filesystems as Manila shares.  It supports two
access protocols:

- **WEKAFS** (primary) — the WekaFS kernel POSIX client mounted directly
  on the Manila host.  Sub-250 µs latency, full POSIX semantics, native
  quota enforcement.
- **NFS** (secondary) — standard NFS exports via Weka's built-in NFS
  server.  Suitable for legacy clients that cannot use the POSIX client.

### Why POSIX over NFS?

| Attribute | WekaFS POSIX | NFS |
|-----------|:---:|:---:|
| Latency | < 250 µs | 1–5 ms |
| POSIX compliance | Full | Partial |
| File locking | Yes | Advisory only |
| Adaptive caching | Page + dentry | None |
| Quota enforcement | Native | Post-hoc |
| Throughput | Near bare-metal | Network-bound |

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 OpenStack Manila                 │
│                  (share service)                 │
└───────────────────────┬─────────────────────────┘
                        │  Manila ShareDriver API
                        ▼
┌─────────────────────────────────────────────────┐
│              WekaShareDriver                    │
│          (manila/share/drivers/weka/)           │
│                                                 │
│  ┌──────────────────┐  ┌─────────────────────┐ │
│  │  WekaApiClient   │  │     WekaMount        │ │
│  │  (client.py)     │  │     (posix.py)       │ │
│  │                  │  │                     │ │
│  │  REST API v2     │  │  mount -t wekafs    │ │
│  │  port 14000      │  │  /proc/mounts check │ │
│  └────────┬─────────┘  └──────────┬──────────┘ │
└───────────┼────────────────────────┼────────────┘
            │ HTTPS                  │ kernel
            ▼                        ▼
┌──────────────────────────────────────────────────┐
│                  Weka Cluster                    │
│                                                 │
│  Filesystems  │  Snapshots  │  NFS  │  Quotas  │
└──────────────────────────────────────────────────┘
```

## Prerequisites

- **Weka cluster version** ≥ 4.2
- **OpenStack Manila** ≥ 2023.1 (Antelope)
- **WekaFS client** installed and loaded on the Manila host:
  ```
  modprobe wekafs
  ```
- Network connectivity from the Manila host to the Weka cluster on
  TCP port **14000** (REST API) and the WekaFS data network.

## Installation

```bash
pip install manila-weka-driver
```

Or directly from source:

```bash
git clone https://github.com/weka/manila-weka-driver
cd manila-weka-driver
pip install -e .
```

## Configuration

### 1. Install the WekaFS kernel module

```bash
# RHEL / Rocky / AlmaLinux
dnf install wekafs

# Ubuntu / Debian
apt-get install wekafs

# Load the module
modprobe wekafs

# Persist across reboots
echo "wekafs" >> /etc/modules-load.d/wekafs.conf
```

### 2. manila.conf example

```ini
[DEFAULT]
enabled_share_backends = weka

[weka]
share_driver = manila.share.drivers.weka.driver:WekaShareDriver
share_backend_name = weka
driver_handles_share_servers = false
snapshot_support = true
create_share_from_snapshot_support = true
revert_to_snapshot_support = true

# --- Connection ---
weka_api_server      = weka-cluster.example.com
weka_api_port        = 14000
weka_ssl_verify      = true

# --- Authentication ---
weka_username        = admin
weka_password        = your-password-here
weka_organization    = Root

# --- Filesystem management ---
weka_filesystem_group = default
weka_share_name_prefix = manila_

# --- POSIX client on Manila host ---
weka_mount_point_base  = /mnt/weka
weka_num_cores         = 1
# weka_net_device      = eth0   # optional: NIC for DPDK mode

# --- API behaviour ---
weka_api_timeout       = 30
weka_max_api_retries   = 3
```

### 3. Configuration reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `weka_api_server` | `HostAddress` | **required** | Hostname or IP of the Weka cluster management endpoint |
| `weka_api_port` | `Port` | `14000` | TCP port for the Weka REST API |
| `weka_ssl_verify` | `Bool` | `true` | Verify the cluster's TLS certificate |
| `weka_username` | `String` | `admin` | API username |
| `weka_password` | `String (secret)` | **required** | API password |
| `weka_organization` | `String` | `Root` | Weka organization name to authenticate against |
| `weka_filesystem_group` | `String` | `default` | Filesystem group for new shares |
| `weka_mount_point_base` | `String` | `/mnt/weka` | Base directory for WekaFS mounts |
| `weka_num_cores` | `Int` (1–19) | `1` | CPU cores for the WekaFS POSIX client |
| `weka_net_device` | `String` | `None` | NIC for DPDK mode (e.g. `eth0`) |
| `weka_posix_mount_timeout` | `Int` | `60` | Seconds to wait for a POSIX mount |
| `weka_api_timeout` | `Int` | `30` | HTTP timeout for API requests (seconds) |
| `weka_max_api_retries` | `Int` | `3` | Maximum retries on transient API errors |
| `weka_share_name_prefix` | `String` | `manila_` | Prefix for Weka filesystem names |

## Supported Operations

| Operation | WEKAFS | NFS | Notes |
|-----------|:------:|:---:|-------|
| create_share | ✓ | ✓ | |
| delete_share | ✓ | ✓ | Idempotent |
| extend_share | ✓ | ✓ | |
| shrink_share | ✓ | ✓ | Guards against data loss |
| ensure_share | ✓ | ✓ | Re-mounts on recovery |
| create_snapshot | ✓ | ✓ | |
| delete_snapshot | ✓ | ✓ | Idempotent |
| revert_to_snapshot | ✓ | ✓ | |
| create_share_from_snapshot | ✓ | ✓ | |
| manage_existing | ✓ | ✓ | |
| unmanage | ✓ | ✓ | |
| get_share_stats | ✓ | ✓ | |

## Access Type Support

| Access Type | WEKAFS | NFS |
|-------------|:------:|:---:|
| `ip` | Recorded (network-level) | ✓ Full enforcement |
| `user` | Recorded (Weka user) | ✗ |
| `cert` | ✗ | ✗ |

## Multi-tenancy

Weka organizations map directly to Manila share types.  Each organization
can have independent storage quotas and separate admin credentials.

To create a Manila share type targeting a specific Weka organization:

```bash
manila type-create weka-org-a false
manila type-key weka-org-a set weka_organization=org-a
```

Then configure a separate Manila backend stanza for each organization
with the appropriate `weka_organization`, `weka_username`, and
`weka_password`.

## Troubleshooting

### `WekaMountError: mount command failed`

The WekaFS kernel module is not loaded.  Run:
```bash
modprobe wekafs
lsmod | grep wekafs   # should show the module
```

### `WekaAuthError: Weka authentication failed`

Check `weka_username`, `weka_password`, and `weka_organization` in
`manila.conf`.  Verify with:
```bash
curl -k -X POST https://<weka-host>:14000/api/v2/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"secret","org":"Root"}'
```

### `ShareShrinkingPossibleDataLoss`

The filesystem contains more data than the target size.  Free space on
the share before shrinking.

### SSL certificate errors

Set `weka_ssl_verify = false` to disable certificate verification in
test environments.  **Do not disable in production.**

### `FileSystemNotFound` errors in ensure_share

The Weka filesystem was deleted outside of Manila.  Either restore the
filesystem or remove the share from Manila:
```bash
manila delete <share-id>
```

## Running Tests

```bash
# Install test dependencies
pip install -r test-requirements.txt

# Unit tests
tox -e py311

# PEP 8 / style check
tox -e pep8

# Coverage report
tox -e cover
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and
submission guidelines.
