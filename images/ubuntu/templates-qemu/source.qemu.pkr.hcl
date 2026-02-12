source "qemu" "ubuntu" {
  # Base image - use existing qcow2 VM
  disk_image       = true
  iso_url          = var.qemu_base_image_path
  iso_checksum     = "none"
  skip_resize_disk = true
  
  # Output
  output_directory = var.qemu_output_directory
  vm_name          = var.qemu_vm_name
  
  # VM Configuration
  cpus             = var.qemu_cpus
  memory           = var.qemu_memory
  accelerator      = "kvm"
  machine_type     = "q35"
  
  # Disk settings - let qemuargs handle the disk for proper boot order
  format           = "qcow2"
  disk_compression = true
  disk_interface   = "virtio"
  
  # Network - use user-mode networking with port forward
  net_device       = "virtio-net"
  
  # SSH communicator
  communicator           = "ssh"
  ssh_username           = var.qemu_ssh_username
  ssh_password           = var.qemu_ssh_password
  ssh_timeout            = "30m"
  ssh_port               = 22
  ssh_handshake_attempts = 100
  ssh_wait_timeout       = "30m"
  
  # Host port forward for SSH (QEMU user-mode networking)
  host_port_min    = 2222
  host_port_max    = 2299
  
  # Boot settings - increase wait time for UEFI boot
  boot_wait        = "60s"
  
  # Headless mode (set to false for debugging)
  headless         = var.qemu_headless
  
  # VNC for debugging (binds to localhost)
  vnc_bind_address = "127.0.0.1"
  vnc_port_min     = 5900
  vnc_port_max     = 5999
  
  # QEMU binary
  qemu_binary      = "qemu-system-x86_64"
  
  # UEFI boot with q35 machine type
  # Note: Must explicitly add disk drive when using qemuargs
  qemuargs = [
    ["-cpu", "host"],
    ["-drive", "if=pflash,format=raw,readonly=on,file=${var.qemu_efi_firmware_code}"],
    ["-drive", "if=pflash,format=raw,file=${var.qemu_efi_firmware_vars}"],
    ["-drive", "file=${var.qemu_output_directory}/${var.qemu_vm_name},format=qcow2,if=virtio,cache=writeback"],
    ["-vga", "virtio"],
  ]
  
  # Shutdown
  shutdown_command = "echo '${var.qemu_ssh_password}' | sudo -S shutdown -P now"
}
