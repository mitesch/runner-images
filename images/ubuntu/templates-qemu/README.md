# ubuntu_zfs

This folder has code to do an auto-install of ubuntu into a new QEMU VM.

Update the settings.json with the desired settings. Update the admin password with a hash using `mkpasswd -m sha-512`.

Run `./run_vm.sh` to run the install. It will shutdown the VM when it is complete.

Then you can run `./run_vm.sh --boot` to boot the VM, log in, and check things.


# Packer

Once you have the `qcow2` disk from the first step, update the `variable.ubuntu.pkr` file if needed.

Run `./build-qemu.sh` to kick off the build.

You can also use
```bash
packer build -var image_os=ubuntu24 -var qemu_ssh_password="" -var qemu_headless=false.
```

Then you can update the settings.json in `ubuntu_zfs` to point the disk at the packer output disk and run `./run_vm.sh --boot` to start up the VM.
