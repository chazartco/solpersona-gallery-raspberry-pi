#!/bin/bash -e
# Build on top of stage2 (Raspberry Pi OS Lite).
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
