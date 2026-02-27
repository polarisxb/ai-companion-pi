# Signal Messaging Setup Guide

This guide covers setting up Signal messaging for your AI companion on a Raspberry Pi (ARM64/aarch64).

## Overview

Signal integration gives your companion the ability to:
- **One-way:** Send you messages when it wakes up
- **Two-way:** Receive your messages and respond (requires a separate phone number)

## Prerequisites

- Java 21+ (`sudo apt install openjdk-21-jdk`)
- signal-cli 0.13.x
- A phone number for Signal registration

## Step 1: Install signal-cli

```bash
# Download signal-cli (check for latest version)
SIGNAL_CLI_VERSION=0.13.21
wget https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}-Linux.tar.gz
sudo tar xf signal-cli-${SIGNAL_CLI_VERSION}-Linux.tar.gz -C /opt
sudo ln -sf /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli /usr/local/bin/signal-cli
```

## Step 2: Compile Native Library for ARM64

signal-cli ships with x86_64 native libraries. On ARM64 (Raspberry Pi), you need to compile libsignal from source.

```bash
# Install build dependencies
sudo apt install -y curl zip protobuf-compiler clang libclang-dev cmake make

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env

# Clone and build libsignal (match the version signal-cli expects)
cd ~
git clone --branch v0.84.0 --depth 1 https://github.com/signalapp/libsignal.git
cd libsignal

# Build the native library (this takes 10-60 minutes on a Pi)
cd java
./gradlew --no-daemon :client:assemble -PskipAndroid=true
```

If the Gradle build fails but the Rust compilation succeeded, find the compiled library:

```bash
find ~/libsignal -name "libsignal_jni*.so" -print
```

You need the `libsignal_jni_aarch64.so` file (or similar). If it built but Gradle can't find it, the Rust output is typically in `target/release/`.

```bash
# Check if Rust built it directly
find ~/libsignal -path "*/release/libsignal_jni*" -print
```

### Patch signal-cli with the compiled library

```bash
# Remove the existing (x86_64) native libraries from the jar
sudo zip -d /opt/signal-cli-${SIGNAL_CLI_VERSION}/lib/libsignal-client-0.84.0.jar '*signal_jni*'

# Add your compiled ARM64 library
# First, put it where the jar expects it:
mkdir -p /tmp/signal-patch
cp /path/to/libsignal_jni_aarch64.so /tmp/signal-patch/libsignal_jni_aarch64.so
cd /tmp/signal-patch
sudo zip -uj /opt/signal-cli-${SIGNAL_CLI_VERSION}/lib/libsignal-client-0.84.0.jar libsignal_jni_aarch64.so
```

### Verify it works

```bash
signal-cli --version
```

If you get the version number without a native library error, you're good.

## Step 3: Register/Link with Signal

### Option A: Link as Secondary Device (Recommended for one-way)

This links your companion to YOUR Signal account. Messages appear as "Note to Self."

```bash
sudo apt install -y qrencode
signal-cli link -n "Companion" 2>&1 | head -1 | qrencode -t ANSI
```

Scan the QR code in Signal on your phone: Settings → Linked Devices → Link New Device.

### Option B: Register a New Number (Required for two-way)

Buy a cheap prepaid phone (TracFone, etc.) and use its number:

```bash
signal-cli -a +1XXXXXXXXXX register
# You'll receive an SMS verification code
signal-cli -a +1XXXXXXXXXX verify CODE
```

## Step 4: Test Messaging

```bash
# Send a test message
signal-cli -a +1YOUR_COMPANION_NUMBER send -m "Hello from your AI companion!" +1YOUR_PHONE_NUMBER
```

## Step 5: Configure the Scripts

Edit `scripts/signal_config.sh` with your numbers:

```bash
COMPANION_NUMBER="+1XXXXXXXXXX"  # Companion's number (or your number if linked)
HUMAN_NUMBER="+1XXXXXXXXXX"       # Your phone number
```

Test the wrapper:

```bash
bash scripts/send_signal.sh "Testing the wrapper script!"
```

## Step 6: Two-Way Messaging (Optional)

For two-way messaging (companion receives and responds to your texts), you need:

1. A separate phone number for the companion (Option B above)
2. The signal listener daemon running:

```bash
pm2 start scripts/signal_listener.sh --name companion-signal --interpreter bash
pm2 save
```

The listener polls for incoming messages every 10 seconds and triggers `handle_message.sh` when you text.

## Troubleshooting

### "Native library not found" error
You need to compile libsignal for ARM64. See Step 2.

### Messages not sending
Check that signal-cli is properly registered:
```bash
signal-cli -a +1YOUR_NUMBER getUserStatus +1YOUR_NUMBER
```

### signal-cli link shows garbled output
Make sure qrencode is installed and your terminal supports ANSI graphics.

### "Unmatched single quote" error with xargs
Avoid apostrophes in messages. The wakeup script uses `tr` and `sed` instead of `xargs` to handle this.

### Listener not responding
Check pm2 logs:
```bash
pm2 logs companion-signal
```
