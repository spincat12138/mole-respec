"""Load the skill ID-to-name mapping extracted from ClientCoreDLL."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree


SKILL_DATA_PATH = Path(__file__).resolve().parent / "ref" / "skills.xml"


@lru_cache(maxsize=1)
def load_skill_names() -> dict[int, str]:
    try:
        root = ElementTree.parse(SKILL_DATA_PATH).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise RuntimeError(f"无法读取技能名称资源：{SKILL_DATA_PATH}") from exc

    if root.tag != "Skills":
        raise ValueError("技能名称资源根节点必须是 Skills")

    result: dict[int, str] = {}
    for skill in root.findall("Skill"):
        raw_id = skill.get("ID")
        name = skill.get("Name")
        if raw_id is None or not name:
            raise ValueError("技能名称资源存在缺少 ID 或 Name 的条目")
        try:
            skill_id = int(raw_id)
        except ValueError as exc:
            raise ValueError(f"技能名称资源包含无效 ID：{raw_id}") from exc
        if skill_id in result:
            raise ValueError(f"技能名称资源包含重复 ID：{skill_id}")
        result[skill_id] = name

    if not result:
        raise ValueError("技能名称资源中没有技能条目")
    return result
