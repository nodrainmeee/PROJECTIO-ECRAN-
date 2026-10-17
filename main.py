"""Point d'entrée (utilisé par PyInstaller et par `python main.py`)."""
import sys

from andromirror.app import main

if __name__ == "__main__":
    sys.exit(main())
