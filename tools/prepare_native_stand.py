"""Create ignored local fixture settings; do not alter existing Docker networks."""

from __future__ import annotations

import argparse
import ipaddress
import json
import secrets
import subprocess

from tools._common import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subnet", required=True, help="A private IPv4 /24 unused by Docker and host/VPN routes"
    )
    args = parser.parse_args()
    subnet = ipaddress.ip_network(args.subnet, strict=True)
    if subnet.version != 4 or subnet.prefixlen != 24 or not subnet.is_private:
        raise ValueError("Choose a private IPv4 /24")
    path = ROOT / "build/native-stand.env"
    if path.exists():
        settings = dict(
            line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
        )
        if settings.get("MATTERGRAM_STAND_SUBNET") != str(subnet):
            raise ValueError(
                "Existing fixture settings use a different subnet; do not overwrite their credentials"
            )
        print("Existing local fixture settings preserved")
        return
    identifiers = subprocess.run(
        ["docker", "network", "ls", "-q"], check=True, capture_output=True, text=True
    ).stdout.split()
    if identifiers:
        networks = json.loads(
            subprocess.run(
                ["docker", "network", "inspect", *identifiers], check=True, capture_output=True, text=True
            ).stdout
        )
        for network in networks:
            for config in network.get("IPAM", {}).get("Config") or []:
                existing = ipaddress.ip_network(config["Subnet"])
                if existing.version == 4 and existing.overlaps(subnet):
                    raise ValueError("Chosen subnet overlaps an existing Docker network")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            f"MATTERGRAM_STAND_DB_PASSWORD={secrets.token_urlsafe(24)}\nMATTERGRAM_STAND_SUBNET={subnet}\n"
        )
    print("Local fixture settings created in ignored build/native-stand.env; credentials omitted")


if __name__ == "__main__":
    main()
