#!/bin/bash
# Paste this into Lightsail's "Add launch script" box when creating the
# instance (Ubuntu 24.04). It runs once, as root, on first boot, and prepares
# the machine; it does not start the app, because the app needs a .env with
# your keys in it first.
#
# Safe to run again by hand (`sudo bash deploy/lightsail-launch.sh`): every
# step checks before it acts. Progress: /var/log/cloud-init-output.log
set -euo pipefail

# 2 GB of swap. The 2 GB instance has enough memory to run the app, but the
# first image build (npm + ~400 MB of wheels) can spike past it, and a build
# killed by the OOM killer fails with a message that does not say so.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Docker Engine and the compose plugin, from Docker's own install script.
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
# Lets the default `ubuntu` user run docker without sudo (from next login).
usermod -aG docker ubuntu

# The code. The repo is public, so no key is needed, and the server is not
# behind the campus firewall that blocks github.com. To deploy a branch other
# than main, put `LABSAGENT_BRANCH=<name>` on a line above this script.
if [ ! -d /home/ubuntu/Lab-Agent ]; then
  git clone --branch "${LABSAGENT_BRANCH:-main}" \
    https://github.com/shehryars715/Lab-Agent.git /home/ubuntu/Lab-Agent
  chown -R ubuntu:ubuntu /home/ubuntu/Lab-Agent
fi
