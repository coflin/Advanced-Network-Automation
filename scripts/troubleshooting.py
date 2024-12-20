#!/usr/bin/python3

from netmiko import ConnectHandler
from sshInfo import sshInfo
from loguru import logger
import os
import difflib
import time


def normalize_config(config):
    """
    Normalize configuration by stripping trailing spaces and standardizing newlines.
    """
    return [line.strip() for line in config.splitlines() if line.strip()]


def parse_contextual_commands(diff_lines):
    """
    Parse the diff lines to generate contextual negation commands.
    Ensures commands are applied under the appropriate configuration context.
    """
    contextual_commands = []
    current_context = None

    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            command = line[1:].strip()  # Remove '+' and strip whitespace
            if command and not command.startswith("!"):  # Ignore comments or empty lines
                if current_context:
                    contextual_commands.append((current_context, f"no {command}"))
                else:
                    contextual_commands.append((None, f"no {command}"))

        elif not line.startswith("-") and not line.startswith("@") and not line.startswith("+"):
            # Detect context (e.g., interface, router config modes)
            stripped_line = line.strip()
            if stripped_line.startswith("interface") or stripped_line.startswith("router"):
                current_context = stripped_line

    return contextual_commands


def compare_configs(net_connect, device_name, current_config, golden_config_path):
    """
    Compare the current configuration with the golden configuration.
    If differences are found, revert extra commands in the correct context
    and apply the golden configuration.
    """
    try:
        # Load golden configuration
        if not os.path.exists(golden_config_path):
            logger.warning(f"No golden configuration found for {device_name}. Skipping comparison.")
            return None

        with open(golden_config_path, "r") as golden_file:
            golden_config = golden_file.read()

        # Normalize configurations
        normalized_current = normalize_config(current_config)
        normalized_golden = normalize_config(golden_config)

        # Compare configurations
        diff = list(difflib.unified_diff(
            normalized_golden,
            normalized_current,
            fromfile="Golden Config",
            tofile="Current Config",
            lineterm=""
        ))

        # Collect differences
        differences = "\n".join(diff)
        if differences:
            logger.warning(f"Differences found for {device_name}:\n{differences}")
            diff_file = f"diffs/{device_name}_diff.txt"
            with open(diff_file, "w") as file:
                file.write(differences)

            # Parse diff to generate contextual negation commands
            contextual_commands = parse_contextual_commands(diff)

            if contextual_commands:
                net_connect.enable()

                # Process each contextual command
                for context, command in contextual_commands:
                    if context:
                        # Combine context and command into a single config set
                        net_connect.send_config_set([context, command])
                    else:
                        # Handle global commands
                        net_connect.send_config_set([command])

            # Apply golden configuration to ensure compliance
            net_connect.send_config_from_file(golden_config_path)
            logger.success("Reverted the config back to golden config")

        else:
            logger.debug(f"No differences found for {device_name}. Configuration is compliant.")

    except Exception as e:
        logger.error(f"Error comparing configs for {device_name}: {e}")


def parse_ospf_neighbors(output):
    """
    Parse the output of 'show ip ospf neighbor' into a list of dictionaries.
    """
    neighbors = []
    lines = output.splitlines()

    # Skip the header and parse remaining lines
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 8:  # Ensure valid OSPF neighbor line
            neighbor = {
                "neighbor_id": parts[0],
                "instance": parts[1],
                "vrf": parts[2],
                "priority": parts[3],
                "state": parts[4],
                "address": parts[6],
                "interface": parts[7],
            }
            neighbors.append(neighbor)

    return neighbors


def load_golden_ospf_neighbors(device_name):
    """
    Load the golden OSPF neighbor state from a file for a specific device.
    """
    golden_file = f"/home/student/git/csci5840/golden-state/{device_name}/ospf"
    if not os.path.exists(golden_file):
        logger.error(f"Golden OSPF neighbors file {golden_file} not found for {device_name}.")
        return []

    neighbors = []
    try:
        with open(golden_file, "r") as file:
            lines = file.readlines()
            for line in lines[1:]:  # Skip the header
                parts = line.split()
                if len(parts) >= 8:  # Ensure valid golden OSPF neighbor line
                    neighbor = {
                        "neighbor_id": parts[0],
                        "instance": parts[1],
                        "vrf": parts[2],
                        "priority": parts[3],
                        "state": parts[4],
                        "address": parts[6],
                        "interface": parts[7],
                    }
                    neighbors.append(neighbor)
    except Exception as e:
        logger.error(f"Error reading golden OSPF neighbors file for {device_name}: {e}")
    return neighbors


def parse_ospf_timers(output):
    """
    Parse the output of 'show ip ospf interface | section Hello' to detect mismatched timers.
    """
    interfaces_to_fix = []
    lines = output.splitlines()

    for line in lines:
        if "is up" in line:
            # Extract interface name
            interface_name = line.split()[0]
        elif "Timer intervals configured" in line:
            # Extract timer values
            parts = line.split(", ")
            hello_timer = int(parts[1].split()[1])
            dead_timer = int(parts[2].split()[1])
            retransmit_timer = int(parts[3].split()[1])

            # Check if timers differ from defaults
            if hello_timer != 10 or dead_timer != 40 or retransmit_timer != 5:
                interfaces_to_fix.append({
                    "interface": interface_name,
                    "hello_timer": hello_timer,
                    "dead_timer": dead_timer,
                    "retransmit_timer": retransmit_timer
                })

    return interfaces_to_fix


def compare_ospf_config(net_connect, device_name, neighbor_id):
    """
    Compare OSPF configuration between a device and its problematic neighbor.
    """
    try:
        logger.debug(f"Comparing OSPF configuration between {device_name} and neighbor {neighbor_id}...")

        # Step 1: Check for 'shutdown' in OSPF configuration
        ospf_config_device = net_connect.send_command("show running-config | section router ospf")
        if "shutdown" in ospf_config_device:
            logger.warning(f"OSPF configuration on {device_name} has 'shutdown'. Fixing...")
            net_connect.send_config_set(["router ospf 1", "no shutdown"])
            logger.debug(f"Removed 'shutdown' from OSPF configuration on {device_name}.")
            time.sleep(10)  # Wait and re-check state
            return

        # Step 2: Check OSPF interface timers
        interface_timers_output = net_connect.send_command("show ip ospf interface | section Hello")
        interfaces_to_fix = parse_ospf_timers(interface_timers_output)

        if interfaces_to_fix:
            logger.warning(f"Timer mismatches detected on {device_name}. Fixing timers...")
            for interface in interfaces_to_fix:
                commands = [
                    f"interface {interface['interface']}",
                    "ip ospf hello-interval 10",
                    "ip ospf dead-interval 40",
                    "ip ospf retransmit-interval 5"
                ]
                net_connect.send_config_set(commands)
                logger.debug(f"Fixed timers for interface {interface['interface']} on {device_name}.")
            time.sleep(10)  # Wait and re-check state
        else:
            logger.debug(f"All OSPF timers on {device_name} are correctly configured.")

    except Exception as e:
        logger.error(f"Error comparing OSPF configuration for {device_name} and {neighbor_id}: {e}")


def compare_ospf_neighbors(device_name, live_neighbors, golden_neighbors, net_connect):
    """
    Compare live OSPF neighbors against the golden state and log issues.
    Trigger OSPF configuration checks if missing neighbors or invalid states are detected.
    """
    # Extract neighbor IDs and states from live and golden neighbors
    live_neighbors_map = {neighbor["neighbor_id"]: neighbor["state"] for neighbor in live_neighbors}
    golden_neighbors_map = {neighbor["neighbor_id"]: neighbor["state"] for neighbor in golden_neighbors}

    # Find missing and unexpected neighbors
    missing_neighbors = set(golden_neighbors_map.keys()) - set(live_neighbors_map.keys())
    unexpected_neighbors = set(live_neighbors_map.keys()) - set(golden_neighbors_map.keys())

    problematic_neighbors = list(missing_neighbors)  # For further checks, if needed

    # Log missing neighbors
    if missing_neighbors:
        logger.error(f"Missing OSPF neighbors on {device_name}:")
        for neighbor_id in missing_neighbors:
            logger.error(f"  {neighbor_id}")

    # Check state mismatches
    for neighbor_id, state in live_neighbors_map.items():
        if neighbor_id in golden_neighbors_map:
            # Ignore states starting with '2' (e.g., '2WAY/DROTHER')
            if not (state.startswith("2") or state.startswith("FULL")):
                logger.error(f"Neighbor {neighbor_id} on {device_name} is in state {state}, expected 2WAY or FULL.")
                problematic_neighbors.append(neighbor_id)

    # Log unexpected neighbors
    if unexpected_neighbors:
        logger.warning(f"Unexpected OSPF neighbors on {device_name}:")
        for neighbor_id in unexpected_neighbors:
            logger.warning(f"  {neighbor_id}")

    # Iterate through troubleshooting for problematic neighbors
    for neighbor_id in problematic_neighbors:
        successfully_fixed = False  # Track if issue is resolved

        # Step 1: Check for 'shutdown' and fix
        compare_ospf_config(net_connect, device_name, neighbor_id)
        logger.debug(f"Re-checking OSPF neighbor: {neighbor_id} on {device_name} after fixing 'shutdown' in 10s...")
        time.sleep(10)  # Wait for state to stabilize
        rechecked_output = net_connect.send_command("show ip ospf neighbor")
        rechecked_neighbors = parse_ospf_neighbors(rechecked_output)
        rechecked_live_neighbors_map = {neighbor["neighbor_id"]: neighbor["state"] for neighbor in rechecked_neighbors}

        if neighbor_id in rechecked_live_neighbors_map and (
            rechecked_live_neighbors_map[neighbor_id].startswith("2") or
            rechecked_live_neighbors_map[neighbor_id].startswith("FULL")
        ):
            logger.success(f"Neighbor {neighbor_id} on {device_name} matches the golden state after fixing 'shutdown'.")
            successfully_fixed = True
            continue

        # Step 2: Fix timers if shutdown didn't resolve the issue
        if not successfully_fixed:
            interface_timers_output = net_connect.send_command("show ip ospf interface | section Hello")
            interfaces_to_fix = parse_ospf_timers(interface_timers_output)
            if interfaces_to_fix:
                logger.warning(f"Timer mismatches detected on {device_name}. Fixing timers...")
                for interface in interfaces_to_fix:
                    commands = [
                        f"interface {interface['interface']}",
                        "ip ospf hello-interval 10",
                        "ip ospf dead-interval 40",
                        "ip ospf retransmit-interval 5"
                    ]
                    net_connect.send_config_set(commands)
                    logger.debug(f"Fixed timers for interface {interface['interface']} on {device_name}.")
                logger.debug(f"Re-checking OSPF neighbors on {device_name} after fixing timers in 10s...")
                time.sleep(10)  # Wait for state to stabilize
                rechecked_output = net_connect.send_command("show ip ospf neighbor")
                rechecked_neighbors = parse_ospf_neighbors(rechecked_output)
                rechecked_live_neighbors_map = {neighbor["neighbor_id"]: neighbor["state"] for neighbor in rechecked_neighbors}

                if neighbor_id in rechecked_live_neighbors_map and (
                    rechecked_live_neighbors_map[neighbor_id].startswith("2") or
                    rechecked_live_neighbors_map[neighbor_id].startswith("FULL")
                ):
                    logger.success(f"Neighbor {neighbor_id} on {device_name} matches the golden state after fixing timers.")
                    successfully_fixed = True
                    continue

        # If all steps fail, log the issue for manual intervention
        if not successfully_fixed:
            logger.error(f"Unable to resolve OSPF issue for neighbor {neighbor_id} on {device_name}. Please check manually.")

    # Log final status
    if not problematic_neighbors:
        logger.info(f"OSPF neighbors on {device_name} match the golden state.")


def check_ospf_neighbors(net_connect, device_name):
    """
    Check the OSPF neighbor state for a device against the golden state.
    """
    try:
        logger.debug(f"Checking OSPF neighbors on {device_name}...")
        live_output = net_connect.send_command("show ip ospf neighbor")
        live_neighbors = parse_ospf_neighbors(live_output)

        # Load the golden OSPF neighbors
        golden_neighbors = load_golden_ospf_neighbors(device_name)

        # Compare live neighbors with the golden state
        compare_ospf_neighbors(device_name, live_neighbors, golden_neighbors, net_connect)

    except Exception as e:
        logger.error(f"Error checking OSPF neighbors on {device_name}: {e}")


def main():
    # Load SSH data
    ssh_data = sshInfo()

    # Validate SSH data
    if not ssh_data:
        logger.error("No data found in sshInfo.csv. Exiting.")
        exit(1)

    # Ensure output directories exist
    output_dir = "current_config"
    diff_dir = "diffs"
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(diff_dir, exist_ok=True)

    # Iterate over devices and retrieve configurations
    for key, value in ssh_data.items():
        device_type = value["Device_Type"]
        ip = value["IP"]
        username = value["Username"]
        password = value["Password"]

        device = {
            "device_type": device_type,
            "host": ip,
            "username": username,
            "password": password,
        }

        try:
            logger.debug(f"Connecting to {key} ({ip})...")
            with ConnectHandler(**device) as net_connect:
                net_connect.enable()
                current_config = net_connect.send_command("show running-config")
                
                # Save current configuration
                config_file = os.path.join(output_dir, f"{key}.cfg")
                with open(config_file, "w") as file:
                    file.write(current_config)

                # Compare with golden configuration
                golden_config_path = f"/home/student/git/csci5840/golden-configs/{key}.cfg"
                compare_configs(net_connect, key, current_config, golden_config_path)

                # Check OSPF neighbors against the golden state
                check_ospf_neighbors(net_connect, key)

        except Exception as e:
            logger.error(f"Failed to process {key} ({ip}): {e}")


if __name__ == "__main__":
    # Start the troubleshooting process
    try:
        main()
    except Exception as e:
        logger.critical(f"Critical error in troubleshooting script: {e}")
