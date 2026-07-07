#!/usr/bin/env bash
set -euo pipefail

cd /opt/signstamp-accountant
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r accountant_tool/requirements-server.txt
# Ultralytics depends on the GUI OpenCV package name. On headless servers we keep
# opencv-python-headless and remove opencv-python to avoid libxcb/X11 dependency.
.venv/bin/pip uninstall -y opencv-python || true
.venv/bin/pip install --force-reinstall --no-deps opencv-python-headless==4.10.0.84
cp accountant_tool/signstamp-accountant.service /etc/systemd/system/signstamp-accountant.service
systemctl daemon-reload
systemctl enable --now signstamp-accountant
