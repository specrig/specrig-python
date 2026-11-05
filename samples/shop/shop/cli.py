import argparse
import sys

from .db import Order, Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shop")
    commands = parser.add_subparsers(dest="command", required=True)
    cancel = commands.add_parser("cancel", help="cancel an order by hand")
    cancel.add_argument("order", type=int)
    args = parser.parse_args(argv)

    with Session.begin() as session:
        order = session.get(Order, args.order)
        if order is None:
            print(f"the shop has no order {args.order}", file=sys.stderr)
            return 1
        session.delete(order)
    print(f"order {args.order} is cancelled")
    return 0
