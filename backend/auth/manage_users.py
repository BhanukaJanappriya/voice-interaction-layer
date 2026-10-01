"""Command line tool for customer and admin accounts, for whoever runs the server.

From the repository root:
    python -m backend.auth.manage_users add --phone "011 234 5678" --name "Nimal Perera" --pin 1234 [--language si]
    python -m backend.auth.manage_users list
    python -m backend.auth.manage_users unlock --phone "011 234 5678"
    python -m backend.auth.manage_users set-pin --phone "011 234 5678" --pin 4321
    python -m backend.auth.manage_users admin-password [--username admin] [--password ...]

admin-password replaces an admin's password and clears its lockout. It is
the way back in when the admin account was created with a different
password than the one in .env (the .env values only seed the very first
admin), or when the password is forgotten.

A command line tool rather than a web page because registering a customer
with a PIN is an operator task for this prototype; there is no self
service registration yet. The PIN can be left out of the command line
(then it is asked for without echo), which keeps it out of shell history.
Migrations run first, so this works on a fresh clone.
"""

import argparse
import getpass
import sys

import config
from auth.admins import set_admin_password
from auth.phone import InvalidPhoneNumber, format_for_display
from auth.users import UserAlreadyExists, create_user, list_users, set_pin, unlock_user
from db.migrate import upgrade_to_head


def _read_pin(given):
    if given:
        return given
    first = getpass.getpass("PIN (4 to 6 digits): ")
    if getpass.getpass("Repeat PIN: ") != first:
        raise ValueError("The two PINs do not match.")
    return first


def _read_password(given):
    if given:
        return given
    first = getpass.getpass("New admin password (at least 10 characters): ")
    if getpass.getpass("Repeat password: ") != first:
        raise ValueError("The two passwords do not match.")
    return first


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.auth.manage_users", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="register a customer")
    add.add_argument("--phone", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--pin", help="4 to 6 digits; asked for without echo when omitted")
    add.add_argument("--language", choices=["en", "si", "ta"])

    commands.add_parser("list", help="list customers (no PINs or hashes)")

    unlock = commands.add_parser("unlock", help="clear a lockout after too many wrong PINs")
    unlock.add_argument("--phone", required=True)

    pin = commands.add_parser("set-pin", help="replace a customer's PIN (also unlocks)")
    pin.add_argument("--phone", required=True)
    pin.add_argument("--pin")

    admin = commands.add_parser("admin-password", help="replace an admin's password (also unlocks)")
    admin.add_argument("--username", default=config.ADMIN_USERNAME or "admin")
    admin.add_argument("--password", help="at least 10 characters; asked for without echo when omitted")

    args = parser.parse_args(argv)
    upgrade_to_head()

    try:
        if args.command == "add":
            user = create_user(args.phone, args.name, _read_pin(args.pin), preferred_language=args.language)
            print(f"Added {user.display_name} ({format_for_display(user.phone_e164)}, id {user.id}).")
        elif args.command == "list":
            users = list_users()
            if not users:
                print("No users yet.")
            for user in users:
                locked = f"locked {user['locked_seconds_left']}s" if user["locked_seconds_left"] else ""
                last = user["last_login_at"].strftime("%Y-%m-%d %H:%M") if user["last_login_at"] else "never"
                print(
                    f"  {user['id']:4}  {format_for_display(user['phone_e164']):14} {user['display_name']:24} "
                    f"lang={user['preferred_language'] or '-':3} failures={user['failed_attempts']} "
                    f"last_login={last} {locked}"
                )
        elif args.command == "unlock":
            if not unlock_user(args.phone):
                print("No user with that number.")
                return 1
            print("Unlocked.")
        elif args.command == "set-pin":
            if not set_pin(args.phone, _read_pin(args.pin)):
                print("No user with that number.")
                return 1
            print("PIN replaced.")
        elif args.command == "admin-password":
            if not set_admin_password(args.username, _read_password(args.password)):
                print(f"No admin named {args.username!r}.")
                return 1
            print(f"Password replaced for admin {args.username!r}; any lockout is cleared.")
    except (InvalidPhoneNumber, UserAlreadyExists, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
