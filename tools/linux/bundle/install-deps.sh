#!/usr/bin/env bash
# Installs what the game needs from the system (Ubuntu / Debian): SDL2 (window, input, sound),
# the Vulkan loader and Mesa's Vulkan drivers (DXVK draws through Vulkan; in a virtual machine
# without a Vulkan GPU driver, Mesa's software one, lavapipe), zenity (the on-screen keyboard's
# text box) and vulkan-tools (vulkaninfo, to see which Vulkan device there is).
set -e
sudo apt-get update
sudo apt-get install -y libsdl2-2.0-0 libvulkan1 mesa-vulkan-drivers zenity vulkan-tools python3
echo
echo "Vulkan devices:"
vulkaninfo --summary 2>/dev/null | grep -E "deviceName|driverName" || echo "  (none found)"
