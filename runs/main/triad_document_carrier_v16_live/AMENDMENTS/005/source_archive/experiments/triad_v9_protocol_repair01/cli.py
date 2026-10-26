"""Use the original explicit command loop with the sealed continuation host."""
from experiments.triad_emergent_v9 import cli as original
from .coordinator import Coordinator


def main(argv=None):
    original.Coordinator = Coordinator
    return original.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
