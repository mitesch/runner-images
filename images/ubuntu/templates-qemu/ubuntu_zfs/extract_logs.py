#!/usr/bin/env python3
"""
Extract installer logs from a VM disk image after a failed install.

Usage:
    python extract_logs.py
    python extract_logs.py --disk /path/to/ubuntu-zfs.qcow2
    python extract_logs.py --output ./my-logs/
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# Log files to extract from the VM
LOG_FILES = [
    # Installer logs
    '/var/log/installer/curtin-install.log',
    '/var/log/installer/subiquity-server-debug.log',
    '/var/log/installer/subiquity-client-debug.log',
    '/var/log/installer/autoinstall-user-data',
    # Cloud-init logs
    '/var/log/cloud-init.log',
    '/var/log/cloud-init-output.log',
    # System logs
    '/var/log/syslog',
    '/var/log/dmesg',
]

# Directories to copy entirely
LOG_DIRS = [
    '/var/log/installer/',
]


def find_nbd_device() -> str:
    """Find an available NBD device."""
    for i in range(16):
        nbd = f'/dev/nbd{i}'
        if Path(nbd).exists():
            # Check if it's in use by looking at size
            result = subprocess.run(
                ['lsblk', '-n', '-o', 'SIZE', '-b', nbd],
                capture_output=True,
                text=True
            )
            size = result.stdout.strip()
            # An unused NBD device has size 0
            if size == '0' or size == '':
                return nbd
    raise RuntimeError(
        "No available NBD device found. Try:\n"
        "  sudo modprobe -r nbd && sudo modprobe nbd max_part=8"
    )


def load_nbd_module():
    """Ensure NBD kernel module is loaded."""
    result = subprocess.run(
        ['lsmod'],
        capture_output=True,
        text=True
    )
    if 'nbd' not in result.stdout:
        print("Loading NBD kernel module...", file=sys.stderr)
        subprocess.run(['sudo', 'modprobe', 'nbd', 'max_part=8'], check=True)


def connect_qcow2(disk_path: Path, nbd_device: str):
    """Connect qcow2 disk to NBD device."""
    print(f"Connecting {disk_path} to {nbd_device}...", file=sys.stderr)
    subprocess.run([
        'sudo', 'qemu-nbd',
        '--connect', nbd_device,
        '--read-only',
        str(disk_path)
    ], check=True)


def disconnect_qcow2(nbd_device: str):
    """Disconnect qcow2 from NBD device."""
    print(f"Disconnecting {nbd_device}...", file=sys.stderr)
    subprocess.run([
        'sudo', 'qemu-nbd',
        '--disconnect', nbd_device
    ], capture_output=True)


def find_partitions(nbd_device: str) -> list[str]:
    """Find partitions on the NBD device."""
    result = subprocess.run(
        ['lsblk', '-ln', '-o', 'NAME,FSTYPE', nbd_device],
        capture_output=True,
        text=True
    )
    
    partitions = []
    for line in result.stdout.strip().split('\n'):
        if line:
            parts = line.split()
            if len(parts) >= 1:
                name = parts[0]
                if name != Path(nbd_device).name:
                    partitions.append(f'/dev/{name}')
    
    return partitions


def mount_partition(partition: str, mount_point: Path) -> bool:
    """Try to mount a partition."""
    try:
        subprocess.run([
            'sudo', 'mount',
            '-o', 'ro',
            partition,
            str(mount_point)
        ], capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def unmount(mount_point: Path):
    """Unmount a partition."""
    subprocess.run([
        'sudo', 'umount',
        str(mount_point)
    ], capture_output=True)


def copy_logs(mount_point: Path, output_dir: Path) -> list[Path]:
    """Copy log files from mounted filesystem."""
    copied = []
    
    # Copy individual files
    for log_path in LOG_FILES:
        src = mount_point / log_path.lstrip('/')
        if src.exists():
            dst = output_dir / Path(log_path).name
            try:
                subprocess.run([
                    'sudo', 'cp', str(src), str(dst)
                ], check=True)
                subprocess.run([
                    'sudo', 'chown', f'{os.getuid()}:{os.getgid()}', str(dst)
                ], check=True)
                copied.append(dst)
                print(f"  Copied: {log_path}", file=sys.stderr)
            except subprocess.CalledProcessError:
                pass
    
    # Copy directories
    for log_dir in LOG_DIRS:
        src = mount_point / log_dir.lstrip('/')
        if src.exists() and src.is_dir():
            dst = output_dir / 'installer'
            try:
                if dst.exists():
                    shutil.rmtree(dst)
                subprocess.run([
                    'sudo', 'cp', '-r', str(src), str(dst)
                ], check=True)
                subprocess.run([
                    'sudo', 'chown', '-R', f'{os.getuid()}:{os.getgid()}', str(dst)
                ], check=True)
                # List files in the directory
                for f in dst.rglob('*'):
                    if f.is_file():
                        copied.append(f)
                print(f"  Copied directory: {log_dir}", file=sys.stderr)
            except subprocess.CalledProcessError:
                pass
    
    return copied


def try_zfs_import(nbd_device: str, output_dir: Path) -> list[Path]:
    """Try to import ZFS pools from the NBD device and extract logs."""
    
    if not shutil.which('zpool'):
        print("  ZFS tools not installed, skipping ZFS import", file=sys.stderr)
        return []
    
    print("Attempting ZFS pool import...", file=sys.stderr)
    copied_files = []
    imported_pools = []
    
    try:
        # Scan for importable pools on the device
        result = subprocess.run(
            ['sudo', 'zpool', 'import', '-d', nbd_device],
            capture_output=True,
            text=True
        )
        
        # Parse pool names from output
        pools_to_import = []
        for line in result.stdout.splitlines():
            if line.strip().startswith('pool:'):
                pool_name = line.split(':', 1)[1].strip()
                pools_to_import.append(pool_name)
        
        if not pools_to_import:
            print("  No ZFS pools found on device", file=sys.stderr)
            return []
        
        print(f"  Found pools: {pools_to_import}", file=sys.stderr)
        
        # Import pools read-only with alternate root
        with tempfile.TemporaryDirectory() as tmpdir:
            altroot = Path(tmpdir) / 'zfs'
            altroot.mkdir()
            
            for pool in pools_to_import:
                # Use a temporary name to avoid conflicts with existing pools
                temp_pool_name = f"{pool}_extract_{os.getpid()}"
                
                try:
                    print(f"  Importing {pool} as {temp_pool_name}...", file=sys.stderr)
                    subprocess.run([
                        'sudo', 'zpool', 'import',
                        '-d', nbd_device,
                        '-o', 'readonly=on',
                        '-R', str(altroot),
                        '-N',  # Don't mount filesystems yet
                        pool,
                        temp_pool_name
                    ], capture_output=True, check=True)
                    imported_pools.append(temp_pool_name)
                    
                    # Mount the root dataset
                    # List datasets to find root
                    result = subprocess.run(
                        ['sudo', 'zfs', 'list', '-H', '-o', 'name,mountpoint', '-r', temp_pool_name],
                        capture_output=True,
                        text=True
                    )
                    
                    for line in result.stdout.splitlines():
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            dataset = parts[0]
                            mountpoint = parts[1]
                            
                            # Try to mount datasets that look like they contain logs
                            if mountpoint in ['/', '/var', 'legacy', 'none']:
                                try:
                                    mount_path = altroot / dataset.replace('/', '_')
                                    mount_path.mkdir(parents=True, exist_ok=True)
                                    
                                    subprocess.run([
                                        'sudo', 'mount', '-t', 'zfs',
                                        '-o', 'ro',
                                        dataset,
                                        str(mount_path)
                                    ], capture_output=True, check=True)
                                    
                                    # Check for logs
                                    if (mount_path / 'var' / 'log').exists():
                                        print(f"  Found logs in {dataset}", file=sys.stderr)
                                        copied_files.extend(copy_logs(mount_path, output_dir))
                                    elif (mount_path / 'log').exists():
                                        # Might be mounted at /var
                                        var_mount = mount_path
                                        if (var_mount / 'log' / 'installer').exists():
                                            print(f"  Found installer logs in {dataset}", file=sys.stderr)
                                            # Adjust paths for /var mount
                                            for log_path in LOG_FILES:
                                                if log_path.startswith('/var/'):
                                                    src = var_mount / log_path[5:]  # Remove /var/
                                                    if src.exists():
                                                        dst = output_dir / Path(log_path).name
                                                        subprocess.run(['sudo', 'cp', str(src), str(dst)], check=True)
                                                        subprocess.run(['sudo', 'chown', f'{os.getuid()}:{os.getgid()}', str(dst)], check=True)
                                                        copied_files.append(dst)
                                                        print(f"    Copied: {log_path}", file=sys.stderr)
                                    
                                    subprocess.run(['sudo', 'umount', str(mount_path)], capture_output=True)
                                except subprocess.CalledProcessError:
                                    pass
                    
                except subprocess.CalledProcessError as e:
                    print(f"  Failed to import {pool}: {e.stderr}", file=sys.stderr)
        
    finally:
        # Export all imported pools
        for pool in imported_pools:
            print(f"  Exporting {pool}...", file=sys.stderr)
            subprocess.run(['sudo', 'zpool', 'export', pool], capture_output=True)
    
    return copied_files


def extract_logs_from_disk(disk_path: Path, output_dir: Path) -> list[Path]:
    """Extract logs from a qcow2 disk image."""
    
    if not disk_path.exists():
        raise FileNotFoundError(f"Disk image not found: {disk_path}")
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Check for required tools
    if not shutil.which('qemu-nbd'):
        raise RuntimeError(
            "qemu-nbd not found. Install with: sudo apt install qemu-utils"
        )
    
    # Load NBD module
    load_nbd_module()
    
    # Find and connect NBD device
    nbd_device = find_nbd_device()
    
    try:
        connect_qcow2(disk_path, nbd_device)
        
        # Wait for partitions to appear
        import time
        time.sleep(1)
        subprocess.run(['sudo', 'partprobe', nbd_device], capture_output=True)
        time.sleep(1)
        
        # Find partitions
        partitions = find_partitions(nbd_device)
        print(f"Found partitions: {partitions}", file=sys.stderr)
        
        copied_files = []
        
        # Try to mount each partition and look for logs
        with tempfile.TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir) / 'mnt'
            mount_point.mkdir()
            
            for partition in partitions:
                print(f"Trying partition {partition}...", file=sys.stderr)
                if mount_partition(partition, mount_point):
                    try:
                        # Check if this looks like a root filesystem
                        if (mount_point / 'var' / 'log').exists():
                            print(f"  Found root filesystem on {partition}", file=sys.stderr)
                            copied_files.extend(copy_logs(mount_point, output_dir))
                    finally:
                        unmount(mount_point)
        
        # If no logs found via partition mount, try ZFS import
        if not copied_files:
            copied_files = try_zfs_import(nbd_device, output_dir)
        
        return copied_files
        
    finally:
        disconnect_qcow2(nbd_device)


def get_default_disk_path() -> Path:
    """Get disk path from settings.json."""
    script_dir = Path(__file__).parent
    settings_file = script_dir / 'settings.json'
    
    if settings_file.exists():
        with open(settings_file) as f:
            settings = json.load(f)
        return Path(settings.get('vm', {}).get('disk_path', ''))
    
    return Path()


def main():
    parser = argparse.ArgumentParser(
        description='Extract installer logs from VM disk image',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Use disk path from settings.json
  %(prog)s --disk ~/vms/ubuntu.qcow2 # Specify disk path
  %(prog)s --output ./my-logs/       # Custom output directory

Extracted logs will be in ./logs/ by default.
"""
    )
    parser.add_argument(
        '--disk', '-d',
        type=Path,
        default=None,
        help='Path to qcow2 disk image (default: from settings.json)'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        default=None,
        help='Output directory for logs (default: ./logs/<timestamp>/)'
    )
    parser.add_argument(
        '--list-only', '-l',
        action='store_true',
        help='Only list log file locations, do not extract'
    )
    
    args = parser.parse_args()
    
    if args.list_only:
        print("Log files that will be extracted:")
        for f in LOG_FILES:
            print(f"  {f}")
        for d in LOG_DIRS:
            print(f"  {d}*")
        return 0
    
    # Determine disk path
    disk_path = args.disk
    if disk_path is None:
        disk_path = get_default_disk_path()
    
    if not disk_path or not disk_path.exists():
        print(f"Error: Disk image not found: {disk_path}", file=sys.stderr)
        print("Specify with --disk or ensure settings.json has correct vm.disk_path", file=sys.stderr)
        return 1
    
    # Determine output directory
    output_dir = args.output
    if output_dir is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = Path(__file__).parent / 'logs' / timestamp
    
    print(f"Extracting logs from: {disk_path}", file=sys.stderr)
    print(f"Output directory: {output_dir}", file=sys.stderr)
    print("", file=sys.stderr)
    
    try:
        copied_files = extract_logs_from_disk(disk_path, output_dir)
        
        if copied_files:
            print("", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print("Extracted log files:", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            for f in sorted(set(copied_files)):
                print(f"  {f}")
            print("", file=sys.stderr)
            print("To view the main installer log:", file=sys.stderr)
            curtin_log = output_dir / 'curtin-install.log'
            if curtin_log.exists():
                print(f"  cat {curtin_log}", file=sys.stderr)
            print("", file=sys.stderr)
            print("To share with Copilot, copy the file contents or path.", file=sys.stderr)
        else:
            print("", file=sys.stderr)
            print("No log files found. The disk may be:", file=sys.stderr)
            print("  - Using ZFS root (logs on ZFS datasets)", file=sys.stderr)
            print("  - Not yet installed (no root filesystem)", file=sys.stderr)
            print("  - Encrypted", file=sys.stderr)
            return 1
        
        return 0
        
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
