"""
test_grbl_connection.py — Pi-to-Arduino Communication Diagnostic
24-671 Spot Welder Project

Run this BEFORE the CNC shield arrives to verify that:
  1. The Pi can find and open the Arduino's serial port
  2. GRBL is running and responsive
  3. Two-way communication works (send commands, receive responses)
  4. Status queries return parseable data
  5. Settings can be read and written

Hardware needed: Just the Raspberry Pi + Arduino Uno (USB cable)
No CNC shield, motors, or other hardware required.

Usage:
    python3 test_grbl_connection.py
    python3 test_grbl_connection.py /dev/ttyACM0   # specify port manually
"""

import serial
import serial.tools.list_ports
import time
import sys
import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ANSI colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASS_COUNT = 0
FAIL_COUNT = 0
WARN_COUNT = 0


def log_pass(msg):
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  {GREEN}✓ PASS{RESET}  {msg}")


def log_fail(msg):
    global FAIL_COUNT
    FAIL_COUNT += 1
    print(f"  {RED}✗ FAIL{RESET}  {msg}")


def log_warn(msg):
    global WARN_COUNT
    WARN_COUNT += 1
    print(f"  {YELLOW}⚠ WARN{RESET}  {msg}")


def log_info(msg):
    print(f"  {CYAN}ℹ INFO{RESET}  {msg}")


def section(title):
    print(f"\n{BOLD}{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}{RESET}")


def read_all_lines(ser, timeout=2):
    """Read all available lines within a timeout window."""
    lines = []
    start = time.time()
    while (time.time() - start) < timeout:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                lines.append(line)
                start = time.time()  # Reset timeout on new data
        else:
            time.sleep(0.05)
    return lines


def send_and_receive(ser, command, timeout=3):
    """Send a command and collect all response lines."""
    ser.flushInput()
    ser.write((command.strip() + '\n').encode('utf-8'))
    return read_all_lines(ser, timeout)


# ---------------------------------------------------------------------------
# Test 1: Port Detection
# ---------------------------------------------------------------------------

def test_port_detection(manual_port=None):
    """Scan for Arduino serial ports or use a manually specified one."""
    section("TEST 1: Serial Port Detection")

    if manual_port:
        log_info(f"Using manually specified port: {manual_port}")
        return manual_port

    log_info("Scanning for serial devices...")
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        log_fail("No serial ports found. Is the Arduino plugged in via USB?")
        return None

    # Show all detected ports
    arduino_port = None
    for p in ports:
        desc = f"{p.device}  —  {p.description}"
        print(f"         Found: {desc}")

        # Look for common Arduino identifiers
        if any(keyword in p.description.lower()
               for keyword in ['arduino', 'ch340', 'ft232', 'usb serial', 'acm']):
            arduino_port = p.device

        if any(keyword in (p.vid or 0, )
               for keyword in [0x2341, 0x1A86, 0x0403]):  # Arduino, CH340, FTDI
            arduino_port = p.device

    if arduino_port:
        log_pass(f"Arduino likely on: {arduino_port}")
    else:
        # Fall back to first available port
        arduino_port = ports[0].device
        log_warn(f"No Arduino-specific port found. Trying: {arduino_port}")

    return arduino_port


# ---------------------------------------------------------------------------
# Test 2: Serial Connection
# ---------------------------------------------------------------------------

def test_serial_connection(port):
    """Try to open the serial port and read GRBL's startup message."""
    section("TEST 2: Serial Connection & GRBL Startup")

    try:
        ser = serial.Serial(port, 115200, timeout=1)
        log_pass(f"Serial port opened: {port} @ 115200 baud")
    except serial.SerialException as e:
        log_fail(f"Could not open {port}: {e}")
        log_info("Try: sudo chmod 666 /dev/ttyUSB0  (or add user to dialout group)")
        return None

    # Wait for GRBL to initialize
    log_info("Waiting for GRBL startup (2 seconds)...")
    time.sleep(2)

    # Read startup message
    startup_lines = read_all_lines(ser, timeout=2)

    if not startup_lines:
        log_fail("No startup message received. Is GRBL flashed on the Arduino?")
        log_info("Try pressing the Arduino's reset button and watch for output")
        return ser

    grbl_detected = False
    for line in startup_lines:
        print(f"         Received: \"{line}\"")
        if 'grbl' in line.lower():
            grbl_detected = True

    if grbl_detected:
        log_pass("GRBL startup message detected")
    else:
        log_warn("Got data but didn't see 'Grbl' in startup. Check firmware.")

    return ser


# ---------------------------------------------------------------------------
# Test 3: Command Response (ok / error)
# ---------------------------------------------------------------------------

def test_command_response(ser):
    """Verify GRBL responds to basic commands with 'ok' or 'error'."""
    section("TEST 3: Command / Response Round Trip")

    # Test 1: Empty line (GRBL should respond with 'ok')
    log_info("Sending empty line (should get 'ok')...")
    responses = send_and_receive(ser, '')
    if any('ok' in r for r in responses):
        log_pass("Empty line → 'ok' received")
    else:
        log_fail(f"Expected 'ok', got: {responses}")

    # Test 2: Invalid command (GRBL should respond with 'error')
    log_info("Sending invalid command 'HELLO' (should get 'error')...")
    responses = send_and_receive(ser, 'HELLO')
    if any('error' in r for r in responses):
        log_pass("Invalid command → error response received")
    else:
        log_fail(f"Expected 'error', got: {responses}")

    # Test 3: G-code that won't execute without motors but is valid syntax
    log_info("Sending 'G0 X0 Y0' (valid G-code, should get 'ok')...")
    responses = send_and_receive(ser, 'G0 X0 Y0')
    if any('ok' in r for r in responses):
        log_pass("Valid G-code → 'ok' received")
    elif any('error' in r for r in responses):
        log_warn(f"Got error (may be in alarm state): {responses}")
        log_info("This is normal if GRBL is in alarm — we'll test unlocking next")
    else:
        log_fail(f"Unexpected response: {responses}")


# ---------------------------------------------------------------------------
# Test 4: Status Query
# ---------------------------------------------------------------------------

def test_status_query(ser):
    """Send the '?' real-time command and parse the status report."""
    section("TEST 4: Real-Time Status Query (?)")

    log_info("Sending '?' status query...")
    ser.flushInput()
    ser.write(b'?')
    lines = read_all_lines(ser, timeout=2)

    status_pattern = re.compile(
        r"<(\w+)\|MPos:([-\d.]+),([-\d.]+),([-\d.]+)"
    )

    status_found = False
    for line in lines:
        print(f"         Received: \"{line}\"")
        match = status_pattern.match(line)
        if match:
            status_found = True
            state = match.group(1)
            x, y, z = match.group(2), match.group(3), match.group(4)
            log_pass(f"Status parsed — State: {state}, "
                     f"Position: X={x} Y={y} Z={z}")

            if state == "Alarm":
                log_info("GRBL is in Alarm state (normal without limit switches)")
                log_info("Sending $X to unlock...")
                unlock_resp = send_and_receive(ser, '$X')
                if any('ok' in r for r in unlock_resp):
                    log_pass("Alarm cleared with $X")
                    # Re-query status
                    ser.write(b'?')
                    lines2 = read_all_lines(ser, timeout=2)
                    for l2 in lines2:
                        m2 = status_pattern.match(l2)
                        if m2:
                            log_pass(f"New state after unlock: {m2.group(1)}")

    if not status_found:
        log_fail(f"Could not parse status response: {lines}")
        log_info("Expected format: <State|MPos:X,Y,Z|...>")


# ---------------------------------------------------------------------------
# Test 5: Settings Read/Write
# ---------------------------------------------------------------------------

def test_settings(ser):
    """Read GRBL settings and verify they're parseable."""
    section("TEST 5: GRBL Settings ($$)")

    log_info("Reading settings with '$$'...")
    responses = send_and_receive(ser, '$$')

    settings = {}
    for line in responses:
        if line.startswith('$') and '=' in line:
            try:
                key, val = line.split('=')
                num = int(key[1:])
                settings[num] = float(val)
            except ValueError:
                pass

    if settings:
        log_pass(f"Parsed {len(settings)} settings")

        # Show the ones you'll care about most
        important = {
            100: "X steps/mm",
            101: "Y steps/mm",
            102: "Z steps/mm",
            110: "X max rate (mm/min)",
            111: "Y max rate (mm/min)",
            120: "X acceleration (mm/s²)",
            121: "Y acceleration (mm/s²)",
            22:  "Homing cycle enable",
            20:  "Soft limits enable",
            21:  "Hard limits enable",
        }
        print()
        log_info("Key settings for your project:")
        for num, name in sorted(important.items()):
            if num in settings:
                print(f"           ${num:3d} = {settings[num]:10.3f}  ({name})")

        # Flag potential issues
        if settings.get(22, 0) == 1:
            log_warn("Homing is enabled ($22=1) — GRBL may alarm on startup "
                     "until limit switches are wired. Disable with: $22=0")
        if settings.get(21, 0) == 1:
            log_warn("Hard limits enabled ($21=1) without limit switches wired. "
                     "Disable with: $21=0 to avoid false alarms")
    else:
        log_fail(f"Could not parse settings. Raw response: {responses}")

    # Test writing a setting (we'll write and restore a safe one)
    log_info("Testing setting write (temporarily changing $110)...")
    original_val = settings.get(110)
    if original_val is not None:
        test_val = 999.0
        resp = send_and_receive(ser, f'$110={test_val}')
        if any('ok' in r for r in resp):
            log_pass("Setting write accepted")
            # Restore original
            send_and_receive(ser, f'$110={original_val}')
            log_info(f"Restored $110 to {original_val}")
        else:
            log_fail(f"Setting write failed: {resp}")


# ---------------------------------------------------------------------------
# Test 6: Jog Command Syntax
# ---------------------------------------------------------------------------

def test_jog_syntax(ser):
    """Test that jog commands are accepted (motion won't occur without motors)."""
    section("TEST 6: Jog Command Syntax")

    log_info("Sending jog command '$J=G91 X1 F100'...")
    log_info("(No physical motion expected without CNC shield + motors)")

    responses = send_and_receive(ser, '$J=G91 X1 F100')

    if any('ok' in r for r in responses):
        log_pass("Jog command accepted by GRBL parser")
    elif any('error:9' in r for r in responses):
        log_warn("Error 9: GRBL in alarm/locked state. Try $X first.")
    elif any('error' in r for r in responses):
        log_warn(f"Jog syntax error (may need $X unlock first): {responses}")
    else:
        log_fail(f"Unexpected response: {responses}")


# ---------------------------------------------------------------------------
# Test 7: GRBLController Module Import
# ---------------------------------------------------------------------------

def test_module_import():
    """Verify the grbl_comm module can be imported."""
    section("TEST 7: grbl_comm.py Module Import")

    try:
        from grbl_comm import GRBLController, MachineStatus
        log_pass("GRBLController imported successfully")

        # Verify key methods exist
        methods = ['connect', 'disconnect', 'send_command', 'get_status',
                   'jog', 'move_to', 'home', 'unlock', 'set_zero',
                   'save_current_position', 'execute_weld_sequence']
        missing = [m for m in methods if not hasattr(GRBLController, m)]
        if not missing:
            log_pass(f"All {len(methods)} expected methods present")
        else:
            log_fail(f"Missing methods: {missing}")

    except ImportError as e:
        log_warn(f"Could not import grbl_comm: {e}")
        log_info("Place grbl_comm.py in the same directory as this test script")
    except Exception as e:
        log_fail(f"Import error: {e}")


# ---------------------------------------------------------------------------
# Test 8: Round Trip Latency
# ---------------------------------------------------------------------------

def test_latency(ser, iterations=20):
    """Measure command round-trip time to characterize the serial link."""
    section("TEST 8: Round Trip Latency")

    log_info(f"Measuring round-trip time over {iterations} iterations...")

    times = []
    for i in range(iterations):
        ser.flushInput()
        start = time.time()
        ser.write(b'\n')  # Empty line → 'ok'

        # Wait for response
        while (time.time() - start) < 1:
            if ser.in_waiting:
                ser.readline()
                elapsed = (time.time() - start) * 1000  # ms
                times.append(elapsed)
                break
        else:
            log_warn(f"Timeout on iteration {i + 1}")

    if times:
        avg = sum(times) / len(times)
        min_t = min(times)
        max_t = max(times)
        log_pass(f"Avg: {avg:.1f}ms  |  Min: {min_t:.1f}ms  |  Max: {max_t:.1f}ms")

        if avg < 20:
            log_info("Excellent latency — well within real-time control needs")
        elif avg < 50:
            log_info("Good latency — fine for jog control and positioning")
        else:
            log_warn("High latency — check USB connection and Pi load")
    else:
        log_fail("No successful round trips")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    manual_port = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"\n{BOLD}{'═' * 50}")
    print("  GRBL Communication Diagnostic")
    print(f"  Pi ↔ Arduino (no CNC shield needed)")
    print(f"{'═' * 50}{RESET}")

    # Test 1: Find the port
    port = test_port_detection(manual_port)
    if not port:
        print(f"\n{RED}Cannot continue without a serial port.{RESET}")
        sys.exit(1)

    # Test 2: Open connection
    ser = test_serial_connection(port)
    if not ser:
        print(f"\n{RED}Cannot continue without serial connection.{RESET}")
        sys.exit(1)

    try:
        # Tests 3-6: Communication tests
        test_command_response(ser)
        test_status_query(ser)
        test_settings(ser)
        test_jog_syntax(ser)
        test_module_import()
        test_latency(ser)

    finally:
        ser.close()

    # Summary
    section("SUMMARY")
    total = PASS_COUNT + FAIL_COUNT + WARN_COUNT
    print(f"  {GREEN}Passed: {PASS_COUNT}{RESET}")
    print(f"  {YELLOW}Warnings: {WARN_COUNT}{RESET}")
    print(f"  {RED}Failed: {FAIL_COUNT}{RESET}")
    print()

    if FAIL_COUNT == 0:
        print(f"  {GREEN}{BOLD}All critical tests passed!{RESET}")
        print(f"  Your Pi ↔ Arduino serial link is working.")
        print(f"  Next step: CNC shield arrives → wire motors → real motion.")
    elif FAIL_COUNT <= 2:
        print(f"  {YELLOW}{BOLD}Mostly working — check the failures above.{RESET}")
    else:
        print(f"  {RED}{BOLD}Multiple failures — check wiring and firmware.{RESET}")

    print()


if __name__ == "__main__":
    main()