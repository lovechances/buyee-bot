import sys

if __name__ == "__main__":
    # Manual login does not need the scanner, SQLAlchemy or fashion modules.
    # In particular, do not make it wait on offloaded desktop dependencies.
    if sys.argv[1:2] == ["setup-session"]:
        from .session_setup import main

        raise SystemExit(main(sys.argv[2:]))
    else:
        from .cli import main

        raise SystemExit(main())
