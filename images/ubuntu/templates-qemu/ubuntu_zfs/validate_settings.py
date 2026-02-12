#!/usr/bin/env python3
"""
Validate settings.json for Ubuntu ZFS installation.

Usage:
    python validate_settings.py
    python validate_settings.py --check-system
    python validate_settings.py -s /path/to/settings.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class ValidationError:
    """Represents a validation error."""
    def __init__(self, path: str, message: str, severity: str = "error"):
        self.path = path
        self.message = message
        self.severity = severity  # "error" or "warning"
    
    def __str__(self):
        return f"[{self.severity.upper()}] {self.path}: {self.message}"


class SettingsValidator:
    """Validates settings.json structure and content."""
    
    # Password hash patterns (crypt format)
    HASH_PATTERNS = {
        '$1$': 'MD5',
        '$5$': 'SHA-256',
        '$6$': 'SHA-512',
        '$y$': 'yescrypt',
    }
    
    # SSH key patterns
    SSH_KEY_TYPES = ['ssh-rsa', 'ssh-ed25519', 'ssh-dss', 'ecdsa-sha2-nistp256', 
                     'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521']
    
    # Valid ZFS compression algorithms
    ZFS_COMPRESSION = ['on', 'off', 'lz4', 'gzip', 'gzip-1', 'gzip-2', 'gzip-3',
                       'gzip-4', 'gzip-5', 'gzip-6', 'gzip-7', 'gzip-8', 'gzip-9',
                       'zle', 'lzjb', 'zstd', 'zstd-fast']
    
    def __init__(self, settings: dict, check_system: bool = False):
        self.settings = settings
        self.check_system = check_system
        self.errors: list[ValidationError] = []
    
    def add_error(self, path: str, message: str):
        self.errors.append(ValidationError(path, message, "error"))
    
    def add_warning(self, path: str, message: str):
        self.errors.append(ValidationError(path, message, "warning"))
    
    def get_value(self, path: str, default: Any = None) -> Any:
        """Get a value from settings using dot notation."""
        keys = path.split('.')
        value = self.settings
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
    
    def require_field(self, path: str, expected_type: type = None) -> Any:
        """Validate that a required field exists and optionally check its type."""
        value = self.get_value(path)
        if value is None:
            self.add_error(path, "Required field is missing")
            return None
        
        if expected_type and not isinstance(value, expected_type):
            self.add_error(path, f"Expected {expected_type.__name__}, got {type(value).__name__}")
            return None
        
        return value
    
    def validate_hostname(self, hostname: str, path: str):
        """Validate hostname per RFC 1123."""
        if not hostname:
            return
        
        if len(hostname) > 253:
            self.add_error(path, "Hostname too long (max 253 characters)")
            return
        
        # RFC 1123: alphanumeric, hyphens, dots; must start/end with alphanumeric
        pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
        if not re.match(pattern, hostname):
            self.add_error(path, "Invalid hostname format (RFC 1123)")
    
    def validate_username(self, username: str, path: str):
        """Validate Unix username."""
        if not username:
            return
        
        if len(username) > 32:
            self.add_error(path, "Username too long (max 32 characters)")
            return
        
        # Unix username: lowercase, digits, underscore, hyphen; must start with letter or underscore
        pattern = r'^[a-z_][a-z0-9_-]*$'
        if not re.match(pattern, username):
            self.add_error(path, "Invalid username (lowercase letters, digits, underscore, hyphen; must start with letter or underscore)")
    
    def validate_password_hash(self, hash_str: str, path: str):
        """Validate password hash format."""
        if not hash_str:
            return
        
        # Check for placeholder
        if 'REPLACE' in hash_str or hash_str == '':
            self.add_warning(path, "Password hash appears to be a placeholder")
            return
        
        # Check hash type prefix
        valid_prefix = False
        for prefix, name in self.HASH_PATTERNS.items():
            if hash_str.startswith(prefix):
                valid_prefix = True
                break
        
        if not valid_prefix:
            self.add_error(path, f"Invalid password hash format. Expected crypt format starting with one of: {', '.join(self.HASH_PATTERNS.keys())}")
            return
        
        # SHA-512 format: $6$rounds=N$salt$hash or $6$salt$hash
        if hash_str.startswith('$6$'):
            parts = hash_str.split('$')
            if len(parts) < 4:
                self.add_error(path, "Invalid SHA-512 hash format")
    
    def validate_ssh_key(self, key: str, path: str):
        """Validate SSH public key format."""
        if not key:
            return
        
        # Check for placeholder
        if '...' in key or key.strip() == '':
            self.add_warning(path, "SSH key appears to be a placeholder")
            return
        
        parts = key.split()
        if len(parts) < 2:
            self.add_error(path, "Invalid SSH key format (expected: type key [comment])")
            return
        
        key_type = parts[0]
        if key_type not in self.SSH_KEY_TYPES:
            self.add_error(path, f"Unknown SSH key type: {key_type}. Expected one of: {', '.join(self.SSH_KEY_TYPES)}")
            return
        
        # Basic base64 check for the key data
        key_data = parts[1]
        try:
            import base64
            base64.b64decode(key_data)
        except Exception:
            self.add_error(path, "Invalid SSH key: key data is not valid base64")
    
    def validate_port(self, port: int, path: str):
        """Validate port number."""
        if port is None:
            return
        
        if not isinstance(port, int) or port < 1 or port > 65535:
            self.add_error(path, "Port must be between 1 and 65535")
        elif port < 1024:
            self.add_warning(path, f"Port {port} is a privileged port (< 1024)")
    
    def validate_zfs_pool_name(self, name: str, path: str):
        """Validate ZFS pool name."""
        if not name:
            return
        
        # Reserved names
        reserved = ['mirror', 'raidz', 'raidz1', 'raidz2', 'raidz3', 'spare', 'log', 'cache']
        if name.lower() in reserved:
            self.add_error(path, f"'{name}' is a reserved ZFS keyword")
            return
        
        # Must start with letter, can contain letters, numbers, underscore, hyphen, period
        pattern = r'^[a-zA-Z][a-zA-Z0-9_.-]*$'
        if not re.match(pattern, name):
            self.add_error(path, "Invalid pool name (must start with letter, contain only letters, numbers, _, -, .)")
    
    def validate_disk_device(self, device: str, path: str):
        """Validate disk device path and optionally check if it exists."""
        if not device:
            return
        
        if not device.startswith('/dev/'):
            self.add_error(path, "Disk device must start with /dev/")
            return
        
        if self.check_system:
            device_path = Path(device)
            if not device_path.exists():
                self.add_warning(path, f"Disk device {device} does not exist on this system")
    
    def validate_vm_section(self):
        """Validate VM settings."""
        self.require_field('vm', dict)
        
        name = self.require_field('vm.name', str)
        if name:
            self.validate_hostname(name, 'vm.name')
        
        disk_size = self.require_field('vm.disk_size_gb', int)
        if disk_size is not None and disk_size < 8:
            self.add_error('vm.disk_size_gb', "Disk size must be at least 8 GB for Ubuntu with ZFS")
        
        memory = self.require_field('vm.memory_mb', int)
        if memory is not None and memory < 1024:
            self.add_warning('vm.memory_mb', "Memory should be at least 1024 MB for Ubuntu installation")
        
        cpus = self.require_field('vm.cpus', int)
        if cpus is not None and cpus < 1:
            self.add_error('vm.cpus', "Must have at least 1 CPU")
        
        firmware = self.get_value('vm.firmware')
        if firmware and firmware not in ['uefi', 'bios']:
            self.add_error('vm.firmware', "Firmware must be 'uefi' or 'bios'")
    
    def validate_network_section(self):
        """Validate network settings."""
        self.require_field('network', dict)
        
        hostname = self.require_field('network.hostname', str)
        if hostname:
            self.validate_hostname(hostname, 'network.hostname')
        
        use_dhcp = self.get_value('network.use_dhcp')
        if use_dhcp is False:
            # Static IP required
            static_ip = self.get_value('network.static_ip')
            if not static_ip:
                self.add_error('network.static_ip', "Static IP required when use_dhcp is false")
            else:
                # Basic IP validation
                ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$'
                if not re.match(ip_pattern, static_ip):
                    self.add_error('network.static_ip', "Invalid IP address format")
    
    def validate_user_section(self):
        """Validate user settings."""
        self.require_field('user', dict)
        
        username = self.require_field('user.username', str)
        if username:
            self.validate_username(username, 'user.username')
        
        password_hash = self.require_field('user.password_hash', str)
        if password_hash:
            self.validate_password_hash(password_hash, 'user.password_hash')
        
        ssh_key = self.get_value('user.ssh_public_key')
        if ssh_key:
            self.validate_ssh_key(ssh_key, 'user.ssh_public_key')
        
        groups = self.get_value('user.groups')
        if groups is not None and not isinstance(groups, list):
            self.add_error('user.groups', "Groups must be a list")
    
    def validate_zfs_section(self):
        """Validate ZFS settings."""
        zfs = self.get_value('zfs')
        if zfs is None:
            return  # ZFS section is optional
        
        if not isinstance(zfs, dict):
            self.add_error('zfs', "Must be a dictionary")
            return
        
        enabled = self.get_value('zfs.enabled')
        if enabled is not None and not isinstance(enabled, bool):
            self.add_error('zfs.enabled', "Must be a boolean (true/false)")
        
        install_latest = self.get_value('zfs.install_latest')
        if install_latest is not None:
            if not isinstance(install_latest, bool):
                self.add_error('zfs.install_latest', "Must be a boolean (true/false)")
            elif install_latest and not enabled:
                self.add_warning('zfs.install_latest', "install_latest is true but zfs.enabled is false")
        
        compression = self.get_value('zfs.compression')
        if compression is not None:
            if not isinstance(compression, str):
                self.add_error('zfs.compression', "Must be a string")
            elif compression and compression not in self.ZFS_COMPRESSION:
                self.add_error('zfs.compression', f"Invalid compression algorithm: '{compression}'. Valid options: {', '.join(self.ZFS_COMPRESSION)}")
            elif compression and not enabled:
                self.add_warning('zfs.compression', "compression is set but zfs.enabled is false")
    
    def validate_ssh_section(self):
        """Validate SSH settings."""
        self.require_field('ssh', dict)
        
        port = self.require_field('ssh.port', int)
        if port:
            self.validate_port(port, 'ssh.port')
        
        for field in ['permit_root_login', 'password_authentication', 'pubkey_authentication']:
            value = self.get_value(f'ssh.{field}')
            if value is not None and not isinstance(value, bool):
                self.add_error(f'ssh.{field}', "Must be a boolean (true/false)")
    
    def validate_packages(self):
        """Validate packages list."""
        packages = self.get_value('packages')
        if packages is None:
            return
        
        if not isinstance(packages, list):
            self.add_error('packages', "Must be a list of package names")
            return
        
        # Check for required packages
        required = ['zfsutils-linux', 'openssh-server']
        for pkg in required:
            if pkg not in packages:
                self.add_warning('packages', f"Recommended package '{pkg}' is not in the list")
    
    def validate_all(self) -> bool:
        """Run all validations and return True if no errors."""
        self.validate_vm_section()
        self.validate_network_section()
        self.validate_user_section()
        self.validate_zfs_section()
        self.validate_ssh_section()
        self.validate_packages()
        
        # Check locale
        locale = self.get_value('locale')
        if locale and not re.match(r'^[a-z]{2}_[A-Z]{2}(\.[A-Za-z0-9-]+)?$', locale):
            self.add_warning('locale', "Locale format may be invalid (expected: xx_XX or xx_XX.UTF-8)")
        
        # Check timezone
        timezone = self.get_value('timezone')
        if timezone:
            tz_path = Path('/usr/share/zoneinfo') / timezone
            if self.check_system and not tz_path.exists():
                self.add_warning('timezone', f"Timezone '{timezone}' not found in /usr/share/zoneinfo")
        
        return not any(e.severity == "error" for e in self.errors)


def main():
    parser = argparse.ArgumentParser(
        description='Validate settings.json for Ubuntu ZFS installation'
    )
    parser.add_argument(
        '--settings', '-s',
        type=Path,
        default=Path(__file__).parent / 'settings.json',
        help='Path to settings.json (default: settings.json in script directory)'
    )
    parser.add_argument(
        '--check-system',
        action='store_true',
        help='Also check system resources (disk devices, timezones)'
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Only output errors, not warnings'
    )
    
    args = parser.parse_args()
    
    # Load settings
    if not args.settings.exists():
        print(f"Error: Settings file not found: {args.settings}", file=sys.stderr)
        return 1
    
    try:
        with open(args.settings, 'r') as f:
            settings = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in settings file: {e}", file=sys.stderr)
        return 1
    
    # Validate
    validator = SettingsValidator(settings, check_system=args.check_system)
    is_valid = validator.validate_all()
    
    # Output results
    errors = [e for e in validator.errors if e.severity == "error"]
    warnings = [e for e in validator.errors if e.severity == "warning"]
    
    for error in errors:
        print(error, file=sys.stderr)
    
    if not args.quiet:
        for warning in warnings:
            print(warning, file=sys.stderr)
    
    # Summary
    if is_valid and not warnings:
        print("✓ Settings validation passed", file=sys.stderr)
    elif is_valid:
        print(f"✓ Settings valid with {len(warnings)} warning(s)", file=sys.stderr)
    else:
        print(f"✗ Validation failed: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
    
    return 0 if is_valid else 1


if __name__ == '__main__':
    sys.exit(main())
