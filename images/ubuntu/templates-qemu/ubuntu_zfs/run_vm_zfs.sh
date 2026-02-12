#!/bin/bash
#
# run_vm.sh - Orchestrate Ubuntu ZFS VM creation and launch
#
# Usage:
#   ./run_vm.sh              # Full setup and launch
#   ./run_vm.sh --dry-run    # Show QEMU command without running
#   ./run_vm.sh --install    # Run installation (attach ISOs)
#   ./run_vm.sh --boot       # Boot existing VM (no ISOs)
#

set -e

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default paths
SETTINGS_FILE="${SCRIPT_DIR}/settings_zfs.json"
CLOUD_CONFIG_TEMPLATE="${SCRIPT_DIR}/cloud-config.yaml"
USER_DATA_FILE="${SCRIPT_DIR}/user-data"
CIDATA_ISO="${SCRIPT_DIR}/cidata.iso"
VMLINUZ="${SCRIPT_DIR}/vmlinuz"
INITRD="${SCRIPT_DIR}/initrd"

# OVMF firmware paths (try common locations)
OVMF_CODE=""
OVMF_VARS=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Options
DRY_RUN=false
MODE="install"  # install or boot

#######################################
# Print colored message
#######################################
info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

#######################################
# Ask user if they want to clean up old VM data
#######################################
prompt_cleanup() {
    local disk_path ovmf_vars_copy vm_name
    disk_path=$(get_setting "d['vm']['disk_path']")
    vm_name=$(get_setting "d['vm']['name']")
    ovmf_vars_copy="${SCRIPT_DIR}/${vm_name}_OVMF_VARS.fd"
    
    # Check if any VM data exists
    local has_data=false
    if [[ -f "$disk_path" ]] || [[ -f "$ovmf_vars_copy" ]]; then
        has_data=true
    fi
    
    if [[ "$has_data" == false ]]; then
        return 0
    fi
    
    echo "Existing VM data found:"
    [[ -f "$disk_path" ]] && echo "  - Disk: $disk_path"
    [[ -f "$ovmf_vars_copy" ]] && echo "  - UEFI vars: $ovmf_vars_copy"
    echo ""
    
    read -p "Do you want to delete existing VM data and start fresh? [y/N] " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Cleaning up old VM data..."
        if [[ "$DRY_RUN" == false ]]; then
            [[ -f "$disk_path" ]] && rm -f "$disk_path" && info "Deleted: $disk_path"
            [[ -f "$ovmf_vars_copy" ]] && rm -f "$ovmf_vars_copy" && info "Deleted: $ovmf_vars_copy"
        else
            echo "  [DRY-RUN] Would delete: $disk_path"
            echo "  [DRY-RUN] Would delete: $ovmf_vars_copy"
        fi
        echo ""
    else
        info "Keeping existing VM data"
        echo ""
    fi
}

#######################################
# Parse command line arguments
#######################################
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run|-n)
                DRY_RUN=true
                shift
                ;;
            --install|-i)
                MODE="install"
                shift
                ;;
            --boot|-b)
                MODE="boot"
                shift
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --install, -i   Run installation (attach Ubuntu + cloud-init ISOs)"
                echo "  --boot, -b      Boot existing VM (no ISOs attached)"
                echo "  --dry-run, -n   Show QEMU command without running"
                echo "  --help, -h      Show this help message"
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
}

#######################################
# Read value from settings.json
#######################################
get_setting() {
    python3 -c "import json; d=json.load(open('${SETTINGS_FILE}')); print($1)"
}

#######################################
# Check prerequisites
#######################################
check_prerequisites() {
    info "Checking prerequisites..."
    
    # Check for required commands
    local missing=()
    for cmd in python3 qemu-system-x86_64 qemu-img; do
        if ! command -v "$cmd" &> /dev/null; then
            missing+=("$cmd")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing required commands: ${missing[*]}"
        error "Run: sudo python3 setup_host.py"
        exit 1
    fi
    
    # Check OVMF firmware
    local ovmf_paths=(
        "/usr/share/OVMF/OVMF_CODE_4M.fd:/usr/share/OVMF/OVMF_VARS_4M.fd"
        "/usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd"
        "/usr/share/edk2/ovmf/OVMF_CODE.fd:/usr/share/edk2/ovmf/OVMF_VARS.fd"
        "/usr/share/edk2-ovmf/OVMF_CODE.fd:/usr/share/edk2-ovmf/OVMF_VARS.fd"
        "/usr/share/qemu/OVMF.fd:/usr/share/qemu/OVMF.fd"
        "/usr/share/ovmf/OVMF.fd:/usr/share/ovmf/OVMF.fd"
    )
    
    for pair in "${ovmf_paths[@]}"; do
        local code="${pair%%:*}"
        local vars="${pair##*:}"
        if [[ -f "$code" ]]; then
            OVMF_CODE="$code"
            OVMF_VARS="$vars"
            break
        fi
    done
    
    if [[ -z "$OVMF_CODE" || ! -f "$OVMF_CODE" ]]; then
        error "OVMF firmware not found. Install with: sudo apt install ovmf"
        exit 1
    fi
    
    info "Using OVMF: $OVMF_CODE"
    
    # Check KVM access
    if [[ ! -w /dev/kvm ]]; then
        warn "/dev/kvm not writable - VM will run without hardware acceleration (slow)"
    fi
}

#######################################
# Validate settings
#######################################
validate_settings() {
    info "Validating settings..."
    
    if ! python3 "${SCRIPT_DIR}/validate_settings.py" -s "$SETTINGS_FILE" -q; then
        error "Settings validation failed"
        exit 1
    fi
}

#######################################
# Find or download Ubuntu ISO
#######################################
get_ubuntu_iso() {
    info "Checking for Ubuntu ISO..."
    
    # Look for existing ISO in script directory
    local iso_file
    iso_file=$(find "$SCRIPT_DIR" -maxdepth 1 -name "ubuntu-24.04*-live-server-amd64.iso" -type f | head -1)
    
    if [[ -n "$iso_file" && -f "$iso_file" ]]; then
        info "Found existing ISO: $iso_file"
        UBUNTU_ISO="$iso_file"
        return 0
    fi
    
    # Download ISO
    info "Downloading Ubuntu ISO..."
    if ! python3 "${SCRIPT_DIR}/download_ubuntu_iso.py" -o "$SCRIPT_DIR"; then
        error "Failed to download Ubuntu ISO"
        exit 1
    fi
    
    # Find downloaded ISO
    iso_file=$(find "$SCRIPT_DIR" -maxdepth 1 -name "ubuntu-24.04*-live-server-amd64.iso" -type f | head -1)
    if [[ -z "$iso_file" ]]; then
        error "Ubuntu ISO not found after download"
        exit 1
    fi
    
    UBUNTU_ISO="$iso_file"
}

#######################################
# Extract kernel and initrd from ISO
#######################################
extract_kernel() {
    info "Checking for kernel and initrd..."
    
    if [[ -f "$VMLINUZ" && -f "$INITRD" ]]; then
        info "Kernel and initrd already extracted"
        return 0
    fi
    
    info "Extracting kernel and initrd from ISO..."
    if ! python3 "${SCRIPT_DIR}/extract_kernel.py" "$UBUNTU_ISO" -o "$SCRIPT_DIR"; then
        error "Failed to extract kernel and initrd"
        exit 1
    fi
}

#######################################
# Generate cloud-config and create ISO
#######################################
create_cloud_init_iso() {
    info "Generating cloud-config..."
    
    if ! python3 "${SCRIPT_DIR}/generate_config.py" -s "$SETTINGS_FILE" -t "$CLOUD_CONFIG_TEMPLATE" -o "$USER_DATA_FILE"; then
        error "Failed to generate cloud-config"
        exit 1
    fi
    
    info "Creating cloud-init ISO..."
    local hostname
    hostname=$(get_setting "d['network']['hostname']")
    
    if ! python3 "${SCRIPT_DIR}/create_cloud_init_iso.py" \
        -u "$USER_DATA_FILE" \
        -H "$hostname" \
        --include-network-config \
        -o "$CIDATA_ISO"; then
        error "Failed to create cloud-init ISO"
        exit 1
    fi
}

#######################################
# Create VM disk image
#######################################
create_disk_image() {
    local disk_path disk_size_gb
    disk_path=$(get_setting "d['vm']['disk_path']")
    disk_size_gb=$(get_setting "d['vm']['disk_size_gb']")
    
    # Create directory if needed
    local disk_dir
    disk_dir=$(dirname "$disk_path")
    if [[ ! -d "$disk_dir" ]]; then
        info "Creating disk directory: $disk_dir"
        if [[ "$DRY_RUN" == false ]]; then
            sudo mkdir -p "$disk_dir"
            sudo chown "$(id -u):$(id -g)" "$disk_dir"
        fi
    fi
    
    if [[ -f "$disk_path" ]]; then
        info "Disk image exists: $disk_path"
    else
        info "Creating disk image: $disk_path (${disk_size_gb}G)"
        if [[ "$DRY_RUN" == false ]]; then
            qemu-img create -f qcow2 "$disk_path" "${disk_size_gb}G"
        else
            echo "  [DRY-RUN] qemu-img create -f qcow2 $disk_path ${disk_size_gb}G"
        fi
    fi
    
    VM_DISK="$disk_path"
}

#######################################
# Build and run QEMU command
#######################################
run_qemu() {
    local vm_name memory_mb cpus
    vm_name=$(get_setting "d['vm']['name']")
    memory_mb=$(get_setting "d['vm']['memory_mb']")
    cpus=$(get_setting "d['vm']['cpus']")
    
    # Create a copy of OVMF_VARS for this VM
    local ovmf_vars_copy="${SCRIPT_DIR}/${vm_name}_OVMF_VARS.fd"
    if [[ ! -f "$ovmf_vars_copy" ]]; then
        info "Creating UEFI vars copy: $ovmf_vars_copy"
        if [[ "$DRY_RUN" == false ]]; then
            cp "$OVMF_VARS" "$ovmf_vars_copy"
        fi
    fi
    
    # Build QEMU command
    local qemu_cmd=(
        qemu-system-x86_64
        -name "$vm_name"
        -machine q35,accel=kvm
        -cpu host
        -smp "$cpus"
        -m "$memory_mb"
        
        # UEFI firmware
        -drive "if=pflash,format=raw,readonly=on,file=${OVMF_CODE}"
        -drive "if=pflash,format=raw,file=${ovmf_vars_copy}"
        
        # VM disk
        -drive "file=${VM_DISK},format=qcow2,if=virtio,cache=writeback"
        
        # Network (user mode with port forwarding for SSH)
        -netdev user,id=net0,hostfwd=tcp::2222-:22
        -device virtio-net-pci,netdev=net0
        
        # GTK display window
        -display gtk
        -vga virtio
        
        # RNG device
        -object rng-random,id=rng0,filename=/dev/urandom
        -device virtio-rng-pci,rng=rng0
    )
    
    # Add ISOs for installation mode
    if [[ "$MODE" == "install" ]]; then
        qemu_cmd+=(
            # Direct kernel boot (skip GRUB menu)
            -kernel "$VMLINUZ"
            -initrd "$INITRD"
            
            # Kernel parameters for autoinstall
            # - autoinstall: triggers unattended installation
            # - cloud-init finds cidata ISO automatically by volume label
            -append "autoinstall quiet splash ---"
            
            # Ubuntu installer ISO (primary CD-ROM)
            -cdrom "$UBUNTU_ISO"
            
            # Cloud-init ISO (NoCloud datasource, volume label: cidata)
            -drive "file=${CIDATA_ISO},format=raw,if=virtio,media=cdrom,readonly=on"
        )
    else
        # Boot mode - boot from disk
        qemu_cmd+=(-boot c)
    fi
    
    # Print command
    echo ""
    info "QEMU command:"
    echo "  ${qemu_cmd[*]}"
    echo ""
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY-RUN] Would execute above command"
        return 0
    fi
    
    # Connection info
    info "VM will be accessible via:"
    echo "  - Display: QEMU GTK window"
    echo "  - SSH: ssh -p 2222 $(get_setting "d['user']['username']")@localhost (after installation)"
    echo ""
    info "Close the QEMU window to stop the VM"
    echo ""
    
    # Run QEMU
    "${qemu_cmd[@]}"
}

#######################################
# Cleanup temporary files
#######################################
cleanup() {
    if [[ -f "$USER_DATA_FILE" ]]; then
        rm -f "$USER_DATA_FILE"
    fi
}

#######################################
# Main
#######################################
main() {
    parse_args "$@"
    
    echo "========================================"
    echo " Ubuntu ZFS VM Runner"
    echo "========================================"
    echo ""
    
    if [[ "$DRY_RUN" == true ]]; then
        warn "Running in dry-run mode - no changes will be made"
        echo ""
    fi
    
    check_prerequisites
    validate_settings
    
    # Prompt for cleanup in install mode (unless --boot)
    if [[ "$MODE" == "install" ]]; then
        prompt_cleanup
        get_ubuntu_iso
        extract_kernel
        create_cloud_init_iso
    fi
    
    create_disk_image
    
    # Set trap for cleanup
    trap cleanup EXIT
    
    run_qemu
}

main "$@"
