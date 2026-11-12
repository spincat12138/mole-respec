"""Extract prediction resources from the reference web page."""

from __future__ import annotations

import base64
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ref" / "index.html"
DATA_DIR = ROOT / "ref"


def read_constant(text: str, name: str) -> object:
    marker = f"const {name}="
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"index.html 中缺少 {name} 数据")
    value, _ = json.JSONDecoder().raw_decode(text[start + len(marker) :])
    return value


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    pets = read_constant(text, "PETS")
    egg64 = read_constant(text, "EGG64")
    if not isinstance(pets, dict) or not isinstance(egg64, str):
        raise ValueError("index.html 中的预测数据格式错误")

    (DATA_DIR / "pet_bases.json").write_text(
        json.dumps(pets, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (DATA_DIR / "egg_codes.bin").write_bytes(base64.b64decode(egg64))


if __name__ == "__main__":
    main()
