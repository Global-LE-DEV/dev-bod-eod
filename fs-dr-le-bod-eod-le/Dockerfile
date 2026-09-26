# Use Ubuntu 20.04 as base image
FROM ubuntu:20.04

# Set environment variables to non-interactive
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=off

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-setuptools python3-wheel \
    libssl-dev cron net-tools vim tree snmp wget curl \
    ca-certificates zip unzip && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

# Update CA certificates
RUN update-ca-certificates

# Upgrade pip and install required Python packages
# NOTE: this image previously compiled the checker scripts to standalone
# binaries via PyInstaller. On this build/runtime infrastructure, PyInstaller
# bootloaders are fundamentally broken - even a trivial `import urllib.parse`
# script fails at startup with "Module object for pyimod02_importers is
# NULL!", regardless of PyInstaller version, onefile vs onedir, or BuildKit
# vs the legacy builder. So we run the scripts directly with python3 instead
# of compiling them; do NOT reintroduce a PyInstaller compile step here
# without first confirming the bootloader actually runs on the target host.
RUN pip3 install --upgrade pip setuptools wheel && \
    pip3 install --upgrade certifi

# Set working directory
WORKDIR /data

# Copy application files into /data/overlayFiles/, matching the path
# entrypoint.sh's crontab invokes them from.
COPY ./overlayFiles/ /data/overlayFiles/

# Install Python dependencies and upgrade certifi
RUN pip3 install --upgrade certifi && \
    pip3 install -r /data/overlayFiles/requirements.txt

# Verify certifi installation
RUN python3 -c "import certifi; print('Certifi location:', certifi.where())"

# Ensure necessary directories and log files exist
RUN mkdir -p /var/log && touch /var/log/cron.log

# Copy entrypoint script and make it executable
COPY ./overlayFiles/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Ensure root ownership of all files
RUN chown -R root:root /data

# Set environment variables to use system certificates as fallback
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Start the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
