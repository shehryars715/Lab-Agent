#!/bin/sh
# Paste this into Lightsail's "Add launch script" box when creating the
# instance (Ubuntu 24.04). It runs once, as root, on first boot, and prepares
# the machine; it does not start the app, because the app needs a .env with
# your keys in it first.
#
# Safe to run again by hand (`sudo sh deploy/lightsail-launch.sh`): every
# step checks before it acts. Progress: /var/log/cloud-init-output.log
#
# POSIX sh, not bash, whatever the first line says. Lightsail glues its own
# `#!/bin/sh` setup script IN FRONT of this one, so the shebang above is just
# a comment and dash runs the lot. `set -o pipefail` killed the first launch
# on exactly that. Hence no pipefail, and no pipes that would need it.
set -eu

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
# Downloaded first rather than piped: `curl | sh` without pipefail would run
# an empty script and "succeed" if the download failed.
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
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
