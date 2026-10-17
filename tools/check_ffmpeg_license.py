"""Examine le FFmpeg embarqué dans la roue PyAV installée : licence déclarée, options de compilation,
bibliothèques GPL présentes. Sert à documenter honnêtement ce qui est livré (voir third_party/AVIS-LICENCES.txt).

Usage : python tools/check_ffmpeg_license.py [--strict]
  --strict : code de sortie 1 si une bibliothèque GPL (x264, x265...) est présente dans la roue.
Sans --strict, le résultat est un avertissement (annotation GitHub Actions), jamais un échec.
"""
import os
import re
import sys
from pathlib import Path

GPL_LIBS = {"x264": "libx264 (GPL v2 ou ultérieure)", "x265": "libx265 (GPL v2 ou ultérieure)",
            "xvid": "Xvid (GPL)", "fdk": "fdk-aac (non libre)", "vidstab": "vid.stab (GPL)"}


def main() -> int:
    strict = "--strict" in sys.argv
    try:
        import av
    except Exception as exc:  # noqa: BLE001
        print(f"PyAV introuvable : {exc}")
        return 1
    pkg = Path(av.__file__).resolve().parent
    libs_dir = pkg.parent / "av.libs"
    files = sorted(p.name for p in libs_dir.iterdir()) if libs_dir.is_dir() else []
    print(f"PyAV {av.__version__}")
    print("Versions FFmpeg :", ", ".join(f"{k} {'.'.join(map(str, v))}" for k, v in av.library_versions.items()))
    print(f"Bibliothèques dans {libs_dir.name} : {len(files)}")

    found = []
    for name in files:
        low = name.lower()
        for key, label in GPL_LIBS.items():
            if key in low and label not in found:
                found.append(label)

    config, declared = "", ""
    for name in files:
        if re.match(r"(lib)?avcodec", name, re.IGNORECASE):
            data = (libs_dir / name).read_bytes()
            m = re.search(rb"--prefix=[^\x00]{20,4000}", data)
            if m:
                config = m.group(0).decode("ascii", "replace")
            m = re.search(rb"(?:LGPL|GPL) version [0-9][^\x00]{0,20}", data)
            if m:
                declared = m.group(0).decode("ascii", "replace")
            break
    print("Licence déclarée par libavcodec :", declared or "inconnue")
    print("Options de compilation :", config or "illisibles")
    for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3"):
        print(f"  {flag} : {'oui' if flag in config else 'non'}")
    if found:
        print("Bibliothèques GPL / non libres présentes dans la roue : " + "; ".join(found))
        msg = ("La roue PyAV embarque des bibliothèques GPL (" + ", ".join(found) + "). AndroMirror n'utilise "
               "qu'un décodeur (pas d'encodage), mais ces fichiers sont livrés : voir third_party/AVIS-LICENCES.txt.")
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning title=FFmpeg GPL::{msg}")
        else:
            print("ATTENTION :", msg)
        return 1 if strict else 0
    print("Aucune bibliothèque GPL détectée dans la roue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
