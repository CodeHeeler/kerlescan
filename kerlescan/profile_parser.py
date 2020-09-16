import bitmath
import ipaddress
import re

from ipaddress import AddressValueError
from insights.parsers.installed_rpms import InstalledRpm

from kerlescan.constants import SYSTEM_ID_KEY
from kerlescan.constants import SYSTEM_PROFILE_STRINGS, SYSTEM_PROFILE_INTEGERS
from kerlescan.constants import SYSTEM_PROFILE_BOOLEANS
from kerlescan.constants import SYSTEM_PROFILE_LISTS_OF_STRINGS_ENABLED
from kerlescan.constants import SYSTEM_PROFILE_LISTS_OF_STRINGS_INSTALLED

from kerlescan.exceptions import UnparsableNEVRAError


def parse_profile(system_profile, display_name, logger):
    """
    break complex data structures into more simple structures that can be compared easily.
    display_name is added to data structure for sorting and is later stripped from data structure
    """

    def _parse_lists_of_strings(names, verb):
        """
        helper method to convert lists of strings to comparable facts
        """
        for list_of_strings in names:
            for item in system_profile.get(list_of_strings, []):
                parsed_profile[list_of_strings + "." + item] = verb

    def _parse_running_processes(processes):
        """
        helper method to convert running process lists to facts. We output a
        fact for each process name, with its count as a value. The count is
        returned as a string to match the API spec.
        """
        for process in processes:
            process_fact_name = "running_processes." + process
            if process_fact_name in parsed_profile:
                parsed_profile[process_fact_name] = str(
                    int(parsed_profile[process_fact_name]) + 1
                )
            else:
                parsed_profile[process_fact_name] = "1"

    def _parse_yum_repo(name):
        """
        helper method to convert yum repo objects to comparable facts
        """
        parsed_profile["yum_repos." + name + ".base_url"] = yum_repo.get(
            "base_url", "N/A"
        )
        parsed_profile["yum_repos." + name + ".enabled"] = str(
            yum_repo.get("enabled", "N/A")
        )
        parsed_profile["yum_repos." + name + ".gpgcheck"] = str(
            yum_repo.get("gpgcheck", "N/A")
        )

    def _canonicalize_ipv6_addr(addr):
        """
        helper method to display ipv6 address strings unambiguously. If the address
        is not parsable (for example: 'N/A'), just keep the string as-is.
        """
        try:
            return str(ipaddress.IPv6Address(addr).compressed)
        except AddressValueError:
            return addr

    def _parse_interface(name):
        """
        helper method to convert network interface objects to comparable facts
        """
        ipv6_addresses = [
            _canonicalize_ipv6_addr(addr)
            for addr in interface.get("ipv6_addresses", ["N/A"])
        ]
        parsed_profile["network_interfaces." + name + ".ipv4_addresses"] = ",".join(
            interface.get("ipv4_addresses", ["N/A"])
        )
        parsed_profile["network_interfaces." + name + ".ipv6_addresses"] = ",".join(
            ipv6_addresses
        )
        parsed_profile["network_interfaces." + name + ".mac_address"] = interface.get(
            "mac_address", "N/A"
        )
        parsed_profile["network_interfaces." + name + ".mtu"] = str(
            interface.get("mtu", "N/A")
        )
        parsed_profile["network_interfaces." + name + ".state"] = interface.get(
            "state", "N/A"
        )
        parsed_profile["network_interfaces." + name + ".type"] = interface.get(
            "type", "N/A"
        )

    # start with metadata that we have brought down from the system record
    parsed_profile = {"id": system_profile[SYSTEM_ID_KEY], "name": display_name}
    # add all strings as-is
    for key in SYSTEM_PROFILE_STRINGS:
        parsed_profile[key] = system_profile.get(key, None)

    # add all integers, converting to str
    for key in SYSTEM_PROFILE_INTEGERS | SYSTEM_PROFILE_BOOLEANS:
        parsed_profile[key] = str(system_profile.get(key, "N/A"))

    _parse_lists_of_strings(SYSTEM_PROFILE_LISTS_OF_STRINGS_ENABLED, "enabled")
    _parse_lists_of_strings(SYSTEM_PROFILE_LISTS_OF_STRINGS_INSTALLED, "installed")

    _parse_running_processes(system_profile.get("running_processes", []))

    if "sap_sids" in system_profile:
        parsed_profile["sap_sids"] = ", ".join(
            system_profile["sap_sids"]
        )

    # convert bytes to human readable format
    if "system_memory_bytes" in system_profile:
        with bitmath.format(fmt_str="{value:.2f} {unit}"):
            formatted_size = bitmath.Byte(
                system_profile["system_memory_bytes"]
            ).best_prefix()
            parsed_profile["system_memory"] = str(formatted_size)
        system_profile.pop("system_memory_bytes")

    for package in system_profile.get("installed_packages", []):
        try:
            name, vra = _get_name_vra_from_string(package)
            if name != "gpg-pubkey":
                parsed_profile["installed_packages." + name] = vra
        except UnparsableNEVRAError as e:
            logger.warn(e.message)

    for interface in system_profile.get("network_interfaces", []):
        try:
            name = interface["name"]
            _parse_interface(name)
        except KeyError:
            logger.warn("network interface has no name, skipping")
            continue

    for yum_repo in system_profile.get("yum_repos", []):
        try:
            name = yum_repo["name"]
            _parse_yum_repo(name)
        except KeyError:
            logger.warn("yum repo has no name, skipping")
            continue

    return parsed_profile


def _get_name_vra_from_string(rpm_string):
    """
    small helper to pull name + version/release/arch from string

    This supports two styles: ENVRA and NEVRA. The latter is preferred.
    """
    try:
        if re.match("^[0-9]+:", rpm_string):
            _, remainder = rpm_string.split(":", maxsplit=1)
            rpm = InstalledRpm.from_package(remainder)
        else:
            rpm = InstalledRpm.from_package(rpm_string)
    except TypeError:
        raise UnparsableNEVRAError("unable to parse %s into nevra" % rpm_string)

    vra = rpm.version if rpm.version else ""
    if rpm.release:
        vra = vra + "-" + rpm.release
    if rpm.arch:
        vra = vra + "." + rpm.arch

    return rpm.name, vra


def get_name(system):
    # this mimics how the inventory service modal displays names.
    name = system["id"]
    if system.get("fqdn"):
        name = system.get("fqdn")
    if system.get("display_name"):
        name = system.get("display_name")

    return name
