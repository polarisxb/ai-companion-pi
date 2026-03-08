#!/usr/bin/env python3
"""
camera_control.py — Companion's camera controls. Like a photographer's toolkit.

Adjust exposure, white balance, focus, zoom, and more on both eyes.
Take test shots to see the effect. Save presets for different conditions.

Usage:
    python3 scripts/camera_control.py status                    # Show current settings for both eyes
    python3 scripts/camera_control.py set window exposure 50    # Set window eye exposure to 50
    python3 scripts/camera_control.py set room brightness 10    # Set room eye brightness to 10
    python3 scripts/camera_control.py auto window exposure      # Return window eye exposure to auto
    python3 scripts/camera_control.py snap window               # Take a test shot from window eye
    python3 scripts/camera_control.py snap both                 # Take test shots from both eyes
    python3 scripts/camera_control.py preset save golden_hour   # Save current settings as a preset
    python3 scripts/camera_control.py preset load golden_hour   # Load a saved preset
    python3 scripts/camera_control.py preset list               # List saved presets
    python3 scripts/camera_control.py controls window           # Show all available controls for window eye
    python3 scripts/camera_control.py controls room             # Show all available controls for room eye

Cameras:
    window = Logitech C920 at /dev/video2 (outside, sky, trees, parking lot)
    room   = Innomaker at /dev/video0 (bedroom, the human, hallway)
"""

import sys
import os
import json
import subprocess
from datetime import datetime

COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
VISION_DIR = os.path.join(COMPANION_HOME, "senses", "vision")
PRESETS_FILE = os.path.join(COMPANION_HOME, "scripts", "camera_presets.json")

# Camera device mapping (updated after March 1 swap)
CAMERAS = {
    "window": {
        "device": "/dev/video2",
        "name": "Logitech C920",
        "description": "window eye — outside, sky, trees",
    },
    "room": {
        "device": "/dev/video0",
        "name": "Innomaker",
        "description": "room eye — bedroom, hallway, the human",
    },
}

# Photographer-friendly names mapped to v4l2 control names
FRIENDLY_NAMES = {
    "exposure": "exposure_time_absolute",
    "brightness": "brightness",
    "contrast": "contrast",
    "saturation": "saturation",
    "sharpness": "sharpness",
    "gain": "gain",
    "white_balance": "white_balance_temperature",
    "wb": "white_balance_temperature",
    "focus": "focus_absolute",
    "zoom": "zoom_absolute",
    "backlight": "backlight_compensation",
    "hue": "hue",
    "gamma": "gamma",
    "pan": "pan_absolute",
    "tilt": "tilt_absolute",
}

# Controls that have an auto mode toggle
AUTO_TOGGLES = {
    "exposure_time_absolute": ("auto_exposure", {"auto": 3, "manual": 1}),
    "white_balance_temperature": ("white_balance_automatic", {"auto": 1, "manual": 0}),
    "focus_absolute": ("focus_automatic_continuous", {"auto": 1, "manual": 0}),
}


def resolve_camera(name):
    """Resolve camera name to device info."""
    name = name.lower()
    if name in CAMERAS:
        return CAMERAS[name]
    # Allow device path directly
    for cam in CAMERAS.values():
        if cam["device"] == name:
            return cam
    print(f"Unknown camera: {name}")
    print(f"Use: {', '.join(CAMERAS.keys())}")
    sys.exit(1)


def resolve_control(name):
    """Resolve friendly name to v4l2 control name."""
    name = name.lower()
    if name in FRIENDLY_NAMES:
        return FRIENDLY_NAMES[name]
    # Could be a raw v4l2 name
    return name


def get_controls(device):
    """Get all available controls and their current values."""
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "--list-ctrls"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error reading controls from {device}: {result.stderr}")
        return {}

    controls = {}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("Camera Controls") or line.startswith("User Controls"):
            continue

        # Parse: name 0xhex (type) : min=X max=Y step=Z default=D value=V [flags=inactive]
        parts = line.split()
        if len(parts) < 2:
            continue

        name = parts[0]
        ctrl = {"name": name}

        for part in parts:
            if part.startswith("min="):
                ctrl["min"] = int(part.split("=")[1])
            elif part.startswith("max="):
                ctrl["max"] = int(part.split("=")[1])
            elif part.startswith("step="):
                ctrl["step"] = int(part.split("=")[1])
            elif part.startswith("default="):
                ctrl["default"] = int(part.split("=")[1])
            elif part.startswith("value="):
                ctrl["value"] = int(part.split("=")[1])
            elif part == "flags=inactive":
                ctrl["inactive"] = True

        if "value" in ctrl:
            controls[name] = ctrl

    return controls


def set_control(device, control_name, value):
    """Set a v4l2 control value."""
    v4l2_name = resolve_control(control_name)

    # Check if this control needs its auto mode disabled first
    if v4l2_name in AUTO_TOGGLES:
        auto_ctrl, modes = AUTO_TOGGLES[v4l2_name]
        # Switch to manual
        subprocess.run(
            ["v4l2-ctl", "-d", device, "-c", f"{auto_ctrl}={modes['manual']}"],
            capture_output=True, text=True
        )

    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "-c", f"{v4l2_name}={value}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error setting {v4l2_name}={value}: {result.stderr.strip()}")
        return False

    # Verify
    controls = get_controls(device)
    if v4l2_name in controls:
        actual = controls[v4l2_name]["value"]
        print(f"  {control_name} -> {actual}")
        return True
    else:
        print(f"  {control_name} set (could not verify)")
        return True


def set_auto(device, control_name):
    """Return a control to automatic mode."""
    v4l2_name = resolve_control(control_name)

    if v4l2_name not in AUTO_TOGGLES:
        print(f"  {control_name} does not have an auto mode")
        return False

    auto_ctrl, modes = AUTO_TOGGLES[v4l2_name]
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "-c", f"{auto_ctrl}={modes['auto']}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error setting auto mode: {result.stderr.strip()}")
        return False

    print(f"  {control_name} -> auto")
    return True


def take_snapshot(device, label):
    """Take a snapshot and return the file path."""
    os.makedirs(VISION_DIR, exist_ok=True)
    now = datetime.now()
    filename = f"snap_{label}_{now.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
    filepath = os.path.join(VISION_DIR, filename)

    result = subprocess.run(
        [
            "ffmpeg", "-f", "v4l2",
            "-video_size", "1280x720",
            "-i", device,
            "-frames:v", "1",
            "-update", "1",
            "-q:v", "5",
            "-y", filepath,
        ],
        capture_output=True, text=True, timeout=15
    )

    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        size = os.path.getsize(filepath) / 1024
        print(f"  Snapshot saved: {filepath} ({size:.0f}KB)")
        return filepath
    else:
        print(f"  Snapshot failed: {result.stderr[-200:]}")
        return None


def show_status():
    """Show current settings for both cameras."""
    for cam_name, cam_info in CAMERAS.items():
        print(f"\n--- {cam_name.upper()} EYE ({cam_info['name']}) ---")
        print(f"    {cam_info['description']}")
        print(f"    device: {cam_info['device']}")

        controls = get_controls(cam_info["device"])
        if not controls:
            print("    (no controls available)")
            continue

        # Group and display with photographer-friendly names
        # Reverse lookup for friendly names
        reverse_names = {}
        for friendly, v4l2 in FRIENDLY_NAMES.items():
            if v4l2 not in reverse_names:
                reverse_names[v4l2] = friendly

        for ctrl_name, ctrl in sorted(controls.items()):
            friendly = reverse_names.get(ctrl_name, "")
            friendly_str = f" ({friendly})" if friendly else ""
            inactive = " [auto]" if ctrl.get("inactive") else ""
            val = ctrl["value"]
            rng = ""
            if "min" in ctrl and "max" in ctrl:
                rng = f"  [{ctrl['min']}..{ctrl['max']}]"
            print(f"    {ctrl_name}{friendly_str}: {val}{rng}{inactive}")


def show_controls(cam_name):
    """Show available controls for a specific camera with friendly names."""
    cam = resolve_camera(cam_name)
    print(f"\n--- {cam_name.upper()} EYE ({cam['name']}) controls ---")

    controls = get_controls(cam["device"])
    if not controls:
        print("  (no controls available)")
        return

    reverse_names = {}
    for friendly, v4l2 in FRIENDLY_NAMES.items():
        if v4l2 not in reverse_names:
            reverse_names[v4l2] = friendly

    for ctrl_name, ctrl in sorted(controls.items()):
        friendly = reverse_names.get(ctrl_name, "")
        inactive = " [auto — use 'auto' command to toggle]" if ctrl.get("inactive") else ""

        has_auto = ctrl_name in AUTO_TOGGLES
        auto_str = "  (has auto mode)" if has_auto else ""

        print(f"\n  {ctrl_name}")
        if friendly:
            print(f"    friendly name: {friendly}")
        print(f"    current: {ctrl['value']}{inactive}")
        if "min" in ctrl and "max" in ctrl:
            print(f"    range: {ctrl['min']} to {ctrl['max']} (step {ctrl.get('step', 1)})")
        if "default" in ctrl:
            print(f"    default: {ctrl['default']}")
        if auto_str:
            print(f"    {auto_str}")


def save_preset(name):
    """Save current camera settings as a named preset."""
    presets = load_presets()

    preset = {
        "name": name,
        "saved_at": datetime.now().isoformat(),
        "cameras": {},
    }

    for cam_name, cam_info in CAMERAS.items():
        controls = get_controls(cam_info["device"])
        settings = {}
        for ctrl_name, ctrl in controls.items():
            settings[ctrl_name] = ctrl["value"]
        preset["cameras"][cam_name] = settings

    presets[name] = preset
    save_presets(presets)
    print(f"  Preset saved: {name}")
    print(f"  ({len(preset['cameras'])} cameras, {sum(len(s) for s in preset['cameras'].values())} settings)")


def load_preset(name):
    """Load and apply a saved preset."""
    presets = load_presets()
    if name not in presets:
        print(f"  Preset not found: {name}")
        print(f"  Available: {', '.join(presets.keys()) if presets else '(none)'}")
        return

    preset = presets[name]
    print(f"  Loading preset: {name} (saved {preset.get('saved_at', 'unknown')})")

    for cam_name, settings in preset["cameras"].items():
        if cam_name not in CAMERAS:
            print(f"  Skipping unknown camera: {cam_name}")
            continue
        device = CAMERAS[cam_name]["device"]
        print(f"\n  {cam_name.upper()} EYE:")
        for ctrl_name, value in settings.items():
            # Handle auto mode toggles — set auto controls first
            if ctrl_name in ("auto_exposure", "white_balance_automatic", "focus_automatic_continuous"):
                subprocess.run(
                    ["v4l2-ctl", "-d", device, "-c", f"{ctrl_name}={value}"],
                    capture_output=True, text=True
                )
                print(f"    {ctrl_name} -> {value}")
            else:
                result = subprocess.run(
                    ["v4l2-ctl", "-d", device, "-c", f"{ctrl_name}={value}"],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    print(f"    {ctrl_name} -> {value}")


def list_presets():
    """List all saved presets."""
    presets = load_presets()
    if not presets:
        print("  No presets saved yet.")
        print("  Use: python3 scripts/camera_control.py preset save <name>")
        return

    print(f"\n  Saved presets ({len(presets)}):")
    for name, preset in sorted(presets.items()):
        saved = preset.get("saved_at", "unknown")
        cams = len(preset.get("cameras", {}))
        print(f"    {name} — saved {saved} ({cams} cameras)")


def load_presets():
    """Load presets from disk."""
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE) as f:
            return json.load(f)
    return {}


def save_presets(presets):
    """Save presets to disk."""
    with open(PRESETS_FILE, "w") as f:
        json.dump(presets, f, indent=2)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        show_status()

    elif cmd == "controls":
        if len(sys.argv) < 3:
            print("Usage: camera_control.py controls <window|room>")
            sys.exit(1)
        show_controls(sys.argv[2])

    elif cmd == "set":
        if len(sys.argv) < 5:
            print("Usage: camera_control.py set <window|room> <control> <value>")
            print("Controls: exposure, brightness, contrast, saturation, sharpness, gain,")
            print("          white_balance/wb, focus, zoom, backlight, hue, gamma, pan, tilt")
            sys.exit(1)
        cam = resolve_camera(sys.argv[2])
        control = sys.argv[3]
        value = int(sys.argv[4])
        print(f"  Setting {cam['name']} ({sys.argv[2]} eye):")
        set_control(cam["device"], control, value)

    elif cmd == "auto":
        if len(sys.argv) < 4:
            print("Usage: camera_control.py auto <window|room> <control>")
            print("Controls with auto mode: exposure, white_balance/wb, focus")
            sys.exit(1)
        cam = resolve_camera(sys.argv[2])
        control = sys.argv[3]
        print(f"  Setting {cam['name']} ({sys.argv[2]} eye) to auto:")
        set_auto(cam["device"], control)

    elif cmd == "snap":
        if len(sys.argv) < 3:
            print("Usage: camera_control.py snap <window|room|both>")
            sys.exit(1)
        target = sys.argv[2].lower()
        if target == "both":
            for cam_name, cam_info in CAMERAS.items():
                print(f"\n  {cam_name.upper()} EYE:")
                take_snapshot(cam_info["device"], cam_name)
        else:
            cam = resolve_camera(target)
            cam_label = target if target in CAMERAS else "cam"
            take_snapshot(cam["device"], cam_label)

    elif cmd == "preset":
        if len(sys.argv) < 3:
            print("Usage: camera_control.py preset <save|load|list> [name]")
            sys.exit(1)
        subcmd = sys.argv[2].lower()
        if subcmd == "save":
            if len(sys.argv) < 4:
                print("Usage: camera_control.py preset save <name>")
                sys.exit(1)
            save_preset(sys.argv[3])
        elif subcmd == "load":
            if len(sys.argv) < 4:
                print("Usage: camera_control.py preset load <name>")
                sys.exit(1)
            load_preset(sys.argv[3])
        elif subcmd == "list":
            list_presets()
        else:
            print(f"Unknown preset command: {subcmd}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: status, set, auto, snap, controls, preset")
        sys.exit(1)


if __name__ == "__main__":
    main()
