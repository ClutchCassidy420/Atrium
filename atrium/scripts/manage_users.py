#!/usr/bin/env python3
"""Manage the people who can sign in. Run it yourself on the server, inside the app's container (so the files keep
the right owner):  docker exec -it <container> python /srv/scripts/manage_users.py /data list
Passwords are typed at a hidden prompt and only a hash is stored. Nobody else ever sees them.

    manage_users.py DATA_DIR list
    manage_users.py DATA_DIR add USERNAME ROLE          (asks for the password)
    manage_users.py DATA_DIR passwd USERNAME            (asks for a new password; signs that user out everywhere)
    manage_users.py DATA_DIR role USERNAME ROLE
    manage_users.py DATA_DIR name USERNAME "Display Name"   (what the pages call them, e.g. in the greeting)
    manage_users.py DATA_DIR remove USERNAME            (also signs them out)

DATA_DIR is the app's data folder, where users.json lives (/data inside the container).
Roles are defined in the instance file (default roles: owner, user).
"""
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from auth import Sessions  # noqa: E402
from users import UserError, UserStore  # noqa: E402


def ask_password() -> str:
    pw = getpass.getpass("Password: ")
    if getpass.getpass("Again: ") != pw:
        raise UserError("The two passwords did not match. Nothing was changed.")
    return pw


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    data, cmd, args = argv[1], argv[2], argv[3:]
    store = UserStore(os.path.join(data, "users.json"))
    sessions = Sessions(Path(data) / "sessions.json")
    try:
        if cmd == "list":
            for u in store.all():
                print("%-20s %-10s %s" % (u["username"], u["role"], u.get("name", "")))
        elif cmd == "add" and len(args) == 2:
            store.add(args[0], args[1], password=ask_password())
            print("Added %s as %s." % (args[0], args[1]))
        elif cmd == "passwd" and len(args) == 1:
            store.set_password(args[0], ask_password())
            n = sessions.delete_user(args[0].lower())
            print("Password changed; %d session(s) ended." % n)
        elif cmd == "name" and len(args) == 2:
            store.set_name(args[0], args[1])
            print("Name set.")
        elif cmd == "role" and len(args) == 2:
            store.set_role(args[0], args[1])
            print("Role changed.")
        elif cmd == "remove" and len(args) == 1:
            store.remove(args[0])
            print("Removed; %d session(s) ended." % sessions.delete_user(args[0].lower()))
        else:
            print(__doc__)
            return 2
    except UserError as e:
        print("Error:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
