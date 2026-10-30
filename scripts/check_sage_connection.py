"""Determines which Sage 50 integration route is available on this machine.

Run this on the Allied machine that will host the Bridge, before committing to
a Sage integration approach. It answers one question: can the Bridge write
directly into the Sage company file, or does it use the import-file route?

Background. Sage 50 US has no public web API -- it is absent from Sage's
developer portal entirely. The only direct route is ODBC against the local
Pervasive/Actian database that Sage 50 Desktop installs. The newer Sage 50
Cloud is a hosted platform with no local database, so for that edition the
import-file route is the only option.

    python scripts/check_sage_connection.py

Nothing is written or changed. This only reads.
"""

import os
import platform
import sys
from pathlib import Path

GREEN = "  [ YES ]"
RED = "  [ NO  ]"
INFO = "  [ -- ]"


def check_windows() -> bool:
    ok = platform.system() == "Windows"
    print(f"{GREEN if ok else RED} Running on Windows")
    if not ok:
        print(f"{INFO}   Sage 50 US is Windows-only. Run this on the host machine.")
    return ok


def find_odbc_drivers() -> list:
    """Lists installed ODBC drivers, looking for the Sage/Pervasive one."""
    drivers = []
    try:
        import winreg

        for hive, path in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ODBC\ODBCINST.INI\ODBC Drivers"),
        ):
            try:
                with winreg.OpenKey(hive, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        drivers.append(winreg.EnumValue(key, i)[0])
            except FileNotFoundError:
                continue
    except ImportError:
        pass
    return sorted(set(drivers))


def find_dsns() -> list:
    """Lists configured ODBC data source names."""
    names = []
    try:
        import winreg

        for hive, path in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ODBC\ODBC.INI\ODBC Data Sources"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\ODBC\ODBC.INI\ODBC Data Sources"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ODBC\ODBC.INI\ODBC Data Sources"),
        ):
            try:
                with winreg.OpenKey(hive, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        n, v, _ = winreg.EnumValue(key, i)
                        names.append(f"{n}  (driver: {v})")
            except FileNotFoundError:
                continue
    except ImportError:
        pass
    return sorted(set(names))


def find_sage_install() -> list:
    """Looks for a local Sage 50 / Peachtree installation."""
    found = []
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(r"C:\Sage"),
        Path(r"C:\Peachtree"),
    ]
    for root in roots:
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if child.is_dir() and any(
                    k in child.name.lower() for k in ("sage", "peachtree", "peachw")
                ):
                    found.append(str(child))
        except PermissionError:
            continue
    return sorted(set(found))


def main() -> int:
    print("=" * 72)
    print("  Sage 50 integration check")
    print("=" * 72)

    on_windows = check_windows()

    installs = find_sage_install()
    print(f"{GREEN if installs else RED} Local Sage 50 / Peachtree installation found")
    for p in installs[:6]:
        print(f"{INFO}   {p}")

    drivers = find_odbc_drivers()
    sage_drivers = [d for d in drivers if any(
        k in d.lower() for k in ("pervasive", "actian", "psql", "sage", "peachtree", "btrieve")
    )]
    print(f"{GREEN if sage_drivers else RED} Sage-compatible ODBC driver installed")
    for d in sage_drivers:
        print(f"{INFO}   {d}")
    if drivers and not sage_drivers:
        print(f"{INFO}   {len(drivers)} other ODBC drivers present, none of them Sage's")

    dsns = find_dsns()
    sage_dsns = [d for d in dsns if any(
        k in d.lower() for k in ("sage", "peachtree", "pervasive", "actian")
    )]
    print(f"{GREEN if sage_dsns else RED} Sage ODBC data source configured")
    for d in sage_dsns:
        print(f"{INFO}   {d}")

    print()
    print("-" * 72)
    direct_possible = on_windows and bool(installs) and bool(sage_drivers)

    if direct_possible:
        print("  VERDICT: Direct Sage integration looks AVAILABLE on this machine.")
        print()
        print("  This machine has Sage 50 Desktop and the ODBC driver it needs.")
        print("  The Bridge can be configured to write sales orders and purchase")
        print("  orders straight into the company file.")
        print()
        print("  Still to confirm by hand:")
        print("    - a Sage user account with permission to create orders")
        print("    - the company file path")
        print("    - that this machine stays powered on")
        if not sage_dsns:
            print("    - an ODBC data source still needs creating (driver is present)")
    else:
        print("  VERDICT: Use the IMPORT-FILE route.")
        print()
        missing = []
        if not installs:
            missing.append("no local Sage 50 Desktop installation")
        if not sage_drivers:
            missing.append("no Sage ODBC driver")
        if not on_windows:
            missing.append("not Windows")
        print(f"  Reason: {'; '.join(missing)}.")
        print()
        print("  This is the normal situation on Sage 50 Cloud, which is a hosted")
        print("  platform with no local database to connect to. Sage 50 US has no")
        print("  public web API, so import files are the supported route.")
        print()
        print("  The Bridge already produces correctly formatted Sage import files.")
        print("  Someone imports them into Sage; the typing is still eliminated.")

    print("-" * 72)
    print()
    print("  Either way the Bridge works. This only decides whether the last")
    print("  step is automatic or a click.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
