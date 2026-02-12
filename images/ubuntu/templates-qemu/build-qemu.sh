#!/bin/bash
#
# build-qemu.sh - Build Ubuntu runner image with Packer/QEMU
#
# This script prepares the OVMF_VARS file and runs Packer
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES_DIR="${SCRIPT_DIR}"

# Source paths (from ubuntu_zfs project)
UBUNTU_ZFS_DIR="${SCRIPT_DIR}/ubuntu_zfs"
BASE_IMAGE="${UBUNTU_ZFS_DIR}/ubuntu-zfs.qcow2"
SOURCE_OVMF_VARS="${UBUNTU_ZFS_DIR}/ubuntu-zfs_OVMF_VARS.fd"

# If base image exists in the templates-qemu directory, use that
if [[ -f "${SCRIPT_DIR}/ubuntu-zfs.qcow2" ]]; then
    BASE_IMAGE="${SCRIPT_DIR}/ubuntu-zfs.qcow2"
fi

# Output directory
OUTPUT_DIR="${SCRIPT_DIR}/output-ubuntu"

# Clean previous output
if [[ -d "$OUTPUT_DIR" ]]; then
    echo "Cleaning previous output directory..."
    rm -rf "$OUTPUT_DIR"
fi

# Copy the VM's OVMF_VARS (has correct boot entries for this disk)
OVMF_VARS_COPY="${SCRIPT_DIR}/packer_OVMF_VARS.fd"
echo "Copying VM's OVMF_VARS to ${OVMF_VARS_COPY}..."
cp "$SOURCE_OVMF_VARS" "$OVMF_VARS_COPY"
chmod 644 "$OVMF_VARS_COPY"

# Get SSH password (required)
if [[ -z "$QEMU_SSH_PASSWORD" ]]; then
    echo -n "Enter SSH password for base image: "
    read -s QEMU_SSH_PASSWORD
    echo
fi

# Run Packer with debug to see QEMU command
echo "Running Packer build..."
echo "Base image: ${BASE_IMAGE}"
echo "OVMF_VARS: ${OVMF_VARS_COPY}"
echo ""
echo "=== PACKER LOG (look for qemu-system-x86_64 command) ==="
cd "$TEMPLATES_DIR"
PACKER_LOG=1 packer build \
    -var "image_os=ubuntu24" \
    -var "qemu_ssh_password=${QEMU_SSH_PASSWORD}" \
    -var "qemu_base_image_path=${BASE_IMAGE}" \
    -var "qemu_efi_firmware_vars=${OVMF_VARS_COPY}" \
    -var "qemu_headless=false" \
    "$@" \
    . 2>&1 | tee packer-build.log

echo "Build complete! Output in: ${OUTPUT_DIR}"
echo "Log saved to: packer-build.log"
