#!/usr/bin/env python3
"""
Create a cloud-init ISO image (cidata) for QEMU VM provisioning.

Usage:
    python create_cloud_init_iso.py -u user-data -o cidata.iso
    python create_cloud_init_iso.py -u user-data --hostname myvm -o cidata.iso
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def find_iso_tool() -> tuple[str, list[str]]:
    """Find available ISO creation tool and return command with base args."""
    # Try genisoimage first (most common on Debian/Ubuntu)
    if shutil.which('genisoimage'):
        return 'genisoimage', ['genisoimage']
    
    # Try mkisofs (common on other distros, also provided by cdrtools)
    if shutil.which('mkisofs'):
        return 'mkisofs', ['mkisofs']
    
    # Try xorriso as fallback
    if shutil.which('xorriso'):
        return 'xorriso', ['xorriso', '-as', 'mkisofs']
    
    raise RuntimeError(
        "No ISO creation tool found. Install one of:\n"
        "  Ubuntu/Debian: sudo apt install genisoimage\n"
        "  Fedora/RHEL:   sudo dnf install genisoimage\n"
        "  Arch:          sudo pacman -S cdrtools"
    )


def create_meta_data(hostname: str, instance_id: str = None) -> str:
    """Generate meta-data content for cloud-init."""
    if instance_id is None:
        instance_id = str(uuid.uuid4())
    
    return f"instance-id: {instance_id}\nlocal-hostname: {hostname}\n"


def create_network_config_dhcp() -> str:
    """Generate a simple DHCP network config (v2 format)."""
    return """version: 2
ethernets:
  id0:
    match:
      driver: virtio*
    dhcp4: true
"""


def create_iso(
    user_data_path: Path,
    output_path: Path,
    hostname: str,
    instance_id: str = None,
    network_config_path: Path = None,
    include_network_config: bool = False,
) -> None:
    """Create cloud-init ISO image."""
    
    # Validate user-data file exists
    if not user_data_path.exists():
        raise FileNotFoundError(f"user-data file not found: {user_data_path}")
    
    # Find ISO tool
    tool_name, base_cmd = find_iso_tool()
    
    # Create temporary directory for ISO contents
    with tempfile.TemporaryDirectory(prefix='cidata_') as tmpdir:
        tmppath = Path(tmpdir)
        
        # Copy user-data
        shutil.copy(user_data_path, tmppath / 'user-data')
        
        # Create meta-data
        meta_data_content = create_meta_data(hostname, instance_id)
        (tmppath / 'meta-data').write_text(meta_data_content)
        
        # Handle network-config
        if network_config_path and network_config_path.exists():
            shutil.copy(network_config_path, tmppath / 'network-config')
        elif include_network_config:
            (tmppath / 'network-config').write_text(create_network_config_dhcp())
        
        # Build ISO command
        cmd = base_cmd + [
            '-output', str(output_path),
            '-volid', 'cidata',
            '-joliet',
            '-rock',
            str(tmppath / 'user-data'),
            str(tmppath / 'meta-data'),
        ]
        
        # Add network-config if it exists
        network_config_file = tmppath / 'network-config'
        if network_config_file.exists():
            cmd.append(str(network_config_file))
        
        # Run ISO creation
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ISO creation failed: {e.stderr}")
        
        # Verify output
        if not output_path.exists():
            raise RuntimeError("ISO creation failed: output file not created")
        
        iso_size = output_path.stat().st_size
        print(f"Created {output_path} ({iso_size} bytes) using {tool_name}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Create cloud-init ISO image for QEMU VMs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -u user-data -o cidata.iso
  %(prog)s -u user-data --hostname myvm -o cidata.iso
  %(prog)s -u user-data --network-config network.yaml -o cidata.iso
  %(prog)s -u user-data --include-network-config -o cidata.iso
"""
    )
    parser.add_argument(
        '--user-data', '-u',
        type=Path,
        required=False,
        help='Path to user-data file (cloud-config/autoinstall)'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        default=Path('cidata.iso'),
        help='Output ISO file path (default: cidata.iso)'
    )
    parser.add_argument(
        '--hostname', '-H',
        type=str,
        default='ubuntu',
        help='Hostname for meta-data (default: ubuntu)'
    )
    parser.add_argument(
        '--instance-id', '-i',
        type=str,
        default=None,
        help='Instance ID for meta-data (default: random UUID)'
    )
    parser.add_argument(
        '--network-config', '-n',
        type=Path,
        default=None,
        help='Path to network-config file (optional)'
    )
    parser.add_argument(
        '--include-network-config',
        action='store_true',
        help='Include default DHCP network config if no --network-config provided'
    )
    parser.add_argument(
        '--check-tools',
        action='store_true',
        help='Check for available ISO tools and exit'
    )
    
    args = parser.parse_args()
    
    # Check tools mode
    if args.check_tools:
        try:
            tool_name, _ = find_iso_tool()
            print(f"Found ISO tool: {tool_name}")
            return 0
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    
    # Require user-data for normal operation
    if not args.user_data:
        parser.error("--user-data/-u is required")
    
    try:
        create_iso(
            user_data_path=args.user_data,
            output_path=args.output,
            hostname=args.hostname,
            instance_id=args.instance_id,
            network_config_path=args.network_config,
            include_network_config=args.include_network_config,
        )
        return 0
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
