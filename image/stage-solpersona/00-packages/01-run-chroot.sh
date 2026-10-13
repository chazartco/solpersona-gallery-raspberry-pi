#!/bin/bash -e
# Chromium: Raspberry Pi's build (hardware video decode where the Pi has it,
# V3D GPU compositing). The package was renamed during Bookworm's life.
apt-get install -y --no-install-recommends chromium || apt-get install -y --no-install-recommends chromium-browser
# The image is an appliance: remove what a kiosk never needs and that would
# only take room or wake up.
apt-get purge -y --auto-remove dphys-swapfile triggerhappy || true
