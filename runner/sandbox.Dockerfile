# syntax=docker/dockerfile:1
#
# The image student programs run in when the lab is C, C++, Java or JavaScript
# (Harness v2, D5). Built FROM the app's own image, so Python and its promised
# libraries are identical by construction -- the runner can use this image for
# every program, Python included.
#
#   docker build -t labsagent-app .
#   docker build -f runner/sandbox.Dockerfile -t labsagent-sandbox .
#   runner:  SANDBOX_IMAGE=labsagent-sandbox
#   app:     SANDBOX_LANGUAGES=python,c,cpp,java,javascript
#
# Measured (Phase 1 spike, 2026-11-27): the toolchains add about 0.9 GB; peak
# memory per program is far below the runner's 384 MB cap (javac+JVM 52-61 MiB).
# The runner sets the user (1000:1000), the network (none) and every limit; the
# USER here does not matter, so it is left as the app image's.
ARG APP_IMAGE=labsagent-app
FROM ${APP_IMAGE}
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ libc6-dev openjdk-21-jdk-headless nodejs npm \
 && npm install -g prompt-sync@4.2.0 \
 && apt-get purge -y npm && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /root/.npm
# Node finds the one promised package (prompt-sync) without a package.json.
ENV NODE_PATH=/usr/local/lib/node_modules
