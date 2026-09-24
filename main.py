"""Run the source checkout without installing the project as a package."""

from qpx_electrolyser.app.application import main


if __name__ == "__main__":
    raise SystemExit(main())
