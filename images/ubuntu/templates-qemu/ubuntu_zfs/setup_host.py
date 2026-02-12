#!/usr/bin/env python3
"""
Configure host system for QEMU/KVM virtualization.

Usage:
    sudo python setup_host.py              # Full setup
    python setup_host.py --check-only      # Check status without changes
"""

import argparse
import grp
import os
import pwd
import subprocess
import sys
from pathlib import Path


# Required packages for QEMU/KVM with UEFI support
REQUIRED_PACKAGES = [
    'qemu-system-x86',
    'libvirt-daemon-system',
    'libvirt-clients',
    'virtinst',
    'ovmf',
    'genisoimage',
    'qemu-utils',
]

# Groups needed for virtualization
REQUIRED_GROUPS = ['libvirt', 'kvm']


class SetupResult:
    """Track setup results."""
    def __init__(self):
        self.checks_passed = []
        self.checks_failed = []
        self.actions_taken = []
        self.actions_failed = []
        self.warnings = []


def run_command(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    try:
        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=check,
        )
    except subprocess.CalledProcessError as e:
        if capture:
            return e
        raise


def check_root() -> bool:
    """Check if running as root."""
    return os.geteuid() == 0


def get_real_user() -> tuple[str, int]:
    """Get the real user (not root) who invoked sudo."""
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        return sudo_user, pwd.getpwnam(sudo_user).pw_uid
    
    # Fallback to current user
    uid = os.getuid()
    return pwd.getpwuid(uid).pw_name, uid


def check_cpu_virtualization() -> tuple[bool, str]:
    """Check if CPU supports hardware virtualization."""
    try:
        with open('/proc/cpuinfo', 'r') as f:
            cpuinfo = f.read()
        
        if 'vmx' in cpuinfo:
            return True, "Intel VT-x supported"
        elif 'svm' in cpuinfo:
            return True, "AMD-V supported"
        else:
            return False, "No hardware virtualization support found (vmx/svm)"
    except Exception as e:
        return False, f"Could not read /proc/cpuinfo: {e}"


def check_kvm_modules() -> tuple[bool, str]:
    """Check if KVM kernel modules are loaded."""
    try:
        with open('/proc/modules', 'r') as f:
            modules = f.read()
        
        has_kvm = 'kvm' in modules
        has_kvm_intel = 'kvm_intel' in modules
        has_kvm_amd = 'kvm_amd' in modules
        
        if has_kvm and (has_kvm_intel or has_kvm_amd):
            vendor = "Intel" if has_kvm_intel else "AMD"
            return True, f"KVM modules loaded ({vendor})"
        elif has_kvm:
            return True, "KVM base module loaded"
        else:
            return False, "KVM modules not loaded"
    except Exception as e:
        return False, f"Could not check modules: {e}"


def check_kvm_device() -> tuple[bool, str]:
    """Check if /dev/kvm exists and is accessible."""
    kvm_path = Path('/dev/kvm')
    if not kvm_path.exists():
        return False, "/dev/kvm does not exist"
    
    if os.access(kvm_path, os.R_OK | os.W_OK):
        return True, "/dev/kvm is accessible"
    else:
        return False, "/dev/kvm exists but is not accessible (permission denied)"


def check_package_installed(package: str) -> bool:
    """Check if a package is installed."""
    result = run_command(['dpkg', '-s', package], check=False)
    return result.returncode == 0


def get_missing_packages() -> list[str]:
    """Get list of required packages that are not installed."""
    missing = []
    for pkg in REQUIRED_PACKAGES:
        if not check_package_installed(pkg):
            missing.append(pkg)
    return missing


def install_packages(packages: list[str]) -> tuple[bool, str]:
    """Install packages using apt."""
    if not packages:
        return True, "No packages to install"
    
    # Update package list
    print("Updating package list...", file=sys.stderr)
    result = run_command(['apt-get', 'update', '-qq'], check=False)
    if result.returncode != 0:
        return False, f"apt-get update failed: {result.stderr}"
    
    # Install packages
    print(f"Installing packages: {', '.join(packages)}...", file=sys.stderr)
    result = run_command(
        ['apt-get', 'install', '-y', '-qq'] + packages,
        check=False
    )
    
    if result.returncode != 0:
        return False, f"Package installation failed: {result.stderr}"
    
    return True, f"Installed: {', '.join(packages)}"


def check_user_in_group(username: str, group: str) -> bool:
    """Check if user is in a group."""
    try:
        group_info = grp.getgrnam(group)
        # Check if user is in group's member list
        if username in group_info.gr_mem:
            return True
        # Also check if it's the user's primary group
        user_info = pwd.getpwnam(username)
        return user_info.pw_gid == group_info.gr_gid
    except KeyError:
        return False


def add_user_to_group(username: str, group: str) -> tuple[bool, str]:
    """Add user to a group."""
    result = run_command(['usermod', '-aG', group, username], check=False)
    if result.returncode != 0:
        return False, f"Failed to add {username} to {group}: {result.stderr}"
    return True, f"Added {username} to {group}"


def check_service_status(service: str) -> tuple[bool, bool]:
    """Check if a service is enabled and running. Returns (enabled, running)."""
    enabled_result = run_command(['systemctl', 'is-enabled', service], check=False)
    running_result = run_command(['systemctl', 'is-active', service], check=False)
    
    enabled = enabled_result.returncode == 0
    running = running_result.returncode == 0
    
    return enabled, running


def enable_service(service: str) -> tuple[bool, str]:
    """Enable a systemd service."""
    result = run_command(['systemctl', 'enable', service], check=False)
    if result.returncode != 0:
        return False, f"Failed to enable {service}: {result.stderr}"
    return True, f"Enabled {service}"


def start_service(service: str) -> tuple[bool, str]:
    """Start a systemd service."""
    result = run_command(['systemctl', 'start', service], check=False)
    if result.returncode != 0:
        return False, f"Failed to start {service}: {result.stderr}"
    return True, f"Started {service}"


def check_virsh_connection() -> tuple[bool, str]:
    """Check if virsh can connect to libvirt."""
    result = run_command(['virsh', 'list'], check=False)
    if result.returncode == 0:
        return True, "virsh connection successful"
    else:
        return False, f"virsh connection failed: {result.stderr.strip()}"


def print_status(label: str, success: bool, message: str):
    """Print a status line."""
    icon = "✓" if success else "✗"
    print(f"  {icon} {label}: {message}", file=sys.stderr)


def do_check_only() -> int:
    """Run checks only, no modifications."""
    print("=== System Check ===\n", file=sys.stderr)
    all_ok = True
    
    # CPU virtualization
    print("Hardware:", file=sys.stderr)
    success, msg = check_cpu_virtualization()
    print_status("CPU virtualization", success, msg)
    all_ok = all_ok and success
    
    success, msg = check_kvm_modules()
    print_status("KVM modules", success, msg)
    all_ok = all_ok and success
    
    success, msg = check_kvm_device()
    print_status("KVM device", success, msg)
    all_ok = all_ok and success
    
    # Packages
    print("\nPackages:", file=sys.stderr)
    missing = get_missing_packages()
    if missing:
        print_status("Required packages", False, f"Missing: {', '.join(missing)}")
        all_ok = False
    else:
        print_status("Required packages", True, "All installed")
    
    # User groups
    print("\nUser groups:", file=sys.stderr)
    username, _ = get_real_user()
    for group in REQUIRED_GROUPS:
        try:
            in_group = check_user_in_group(username, group)
            print_status(f"User in {group}", in_group, 
                        f"{username} {'is' if in_group else 'is NOT'} in {group}")
            all_ok = all_ok and in_group
        except KeyError:
            print_status(f"Group {group}", False, "Group does not exist")
            all_ok = False
    
    # Services
    print("\nServices:", file=sys.stderr)
    enabled, running = check_service_status('libvirtd')
    print_status("libvirtd enabled", enabled, "Yes" if enabled else "No")
    print_status("libvirtd running", running, "Yes" if running else "No")
    all_ok = all_ok and enabled and running
    
    # Virsh connection
    print("\nConnectivity:", file=sys.stderr)
    success, msg = check_virsh_connection()
    print_status("virsh connection", success, msg)
    all_ok = all_ok and success
    
    print(file=sys.stderr)
    if all_ok:
        print("✓ System is ready for QEMU/KVM virtualization", file=sys.stderr)
        return 0
    else:
        print("✗ System needs configuration. Run: sudo python setup_host.py", file=sys.stderr)
        return 1


def do_full_setup() -> int:
    """Perform full setup."""
    if not check_root():
        print("Error: This script must be run as root (use sudo)", file=sys.stderr)
        return 1
    
    result = SetupResult()
    username, _ = get_real_user()
    
    print(f"=== QEMU/KVM Host Setup ===", file=sys.stderr)
    print(f"Configuring for user: {username}\n", file=sys.stderr)
    
    # Check hardware
    print("Checking hardware...", file=sys.stderr)
    success, msg = check_cpu_virtualization()
    print_status("CPU virtualization", success, msg)
    if not success:
        print("\nError: Hardware virtualization not supported or not enabled in BIOS", file=sys.stderr)
        return 1
    
    success, msg = check_kvm_modules()
    print_status("KVM modules", success, msg)
    if not success:
        # Try to load modules
        print("Attempting to load KVM modules...", file=sys.stderr)
        run_command(['modprobe', 'kvm'], check=False)
        run_command(['modprobe', 'kvm_intel'], check=False)
        run_command(['modprobe', 'kvm_amd'], check=False)
        success, msg = check_kvm_modules()
        print_status("KVM modules (retry)", success, msg)
        if not success:
            result.warnings.append("KVM modules not loaded - VMs may run slowly without hardware acceleration")
    
    # Install packages
    print("\nChecking packages...", file=sys.stderr)
    missing = get_missing_packages()
    if missing:
        print(f"Missing packages: {', '.join(missing)}", file=sys.stderr)
        success, msg = install_packages(missing)
        print_status("Package installation", success, msg)
        if not success:
            result.actions_failed.append(msg)
        else:
            result.actions_taken.append(msg)
    else:
        print_status("Required packages", True, "All installed")
    
    # Add user to groups
    print("\nConfiguring user groups...", file=sys.stderr)
    groups_changed = False
    for group in REQUIRED_GROUPS:
        if not check_user_in_group(username, group):
            success, msg = add_user_to_group(username, group)
            print_status(f"Add to {group}", success, msg)
            if success:
                result.actions_taken.append(msg)
                groups_changed = True
            else:
                result.actions_failed.append(msg)
        else:
            print_status(f"Group {group}", True, f"{username} already in group")
    
    # Enable and start libvirtd
    print("\nConfiguring services...", file=sys.stderr)
    enabled, running = check_service_status('libvirtd')
    
    if not enabled:
        success, msg = enable_service('libvirtd')
        print_status("Enable libvirtd", success, msg)
        if success:
            result.actions_taken.append(msg)
        else:
            result.actions_failed.append(msg)
    else:
        print_status("libvirtd enabled", True, "Already enabled")
    
    if not running:
        success, msg = start_service('libvirtd')
        print_status("Start libvirtd", success, msg)
        if success:
            result.actions_taken.append(msg)
        else:
            result.actions_failed.append(msg)
    else:
        print_status("libvirtd running", True, "Already running")
    
    # Verify
    print("\nVerifying setup...", file=sys.stderr)
    success, msg = check_kvm_device()
    print_status("KVM device", success, msg)
    
    success, msg = check_virsh_connection()
    print_status("virsh connection", success, msg)
    
    # Summary
    print("\n=== Summary ===", file=sys.stderr)
    if result.actions_taken:
        print(f"Actions completed: {len(result.actions_taken)}", file=sys.stderr)
        for action in result.actions_taken:
            print(f"  • {action}", file=sys.stderr)
    
    if result.actions_failed:
        print(f"\nActions failed: {len(result.actions_failed)}", file=sys.stderr)
        for action in result.actions_failed:
            print(f"  • {action}", file=sys.stderr)
    
    if result.warnings:
        print(f"\nWarnings:", file=sys.stderr)
        for warning in result.warnings:
            print(f"  ⚠ {warning}", file=sys.stderr)
    
    if groups_changed:
        print(f"\n⚠ Group membership changed. You must log out and log back in", file=sys.stderr)
        print(f"  (or run 'newgrp libvirt') for changes to take effect.", file=sys.stderr)
    
    if result.actions_failed:
        print("\n✗ Setup completed with errors", file=sys.stderr)
        return 1
    else:
        print("\n✓ Setup completed successfully", file=sys.stderr)
        return 0


def main():
    parser = argparse.ArgumentParser(
        description='Configure host system for QEMU/KVM virtualization'
    )
    parser.add_argument(
        '--check-only', '-c',
        action='store_true',
        help='Only check system status, do not make changes'
    )
    
    args = parser.parse_args()
    
    if args.check_only:
        return do_check_only()
    else:
        return do_full_setup()


if __name__ == '__main__':
    sys.exit(main())
