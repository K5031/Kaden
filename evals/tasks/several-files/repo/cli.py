import argparse

from store import items


def main(argv=None):
    """The command line: `list` prints the items, one a line, and returns them."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    args = parser.parse_args(argv)
    if args.command == "list":
        shown = items()
        for item in shown:
            print(item)
        return shown
    return None


if __name__ == "__main__":
    main()
