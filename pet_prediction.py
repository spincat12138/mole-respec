"""Pet panel prediction using extracted V14 data and formulas."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from math import ceil, floor, isfinite
from pathlib import Path
from typing import Iterator, Mapping, Sequence


EFFORT_KEYS = ("physique", "strength", "endurance", "quick", "intelligence")
BATTLE_KEYS = ("hp_max", "attack", "defense", "speed", "mp_max")
BATTLE_TOLERANCES = (5, 5, 5, 5, 1)
DATA_DIR = Path(__file__).resolve().parent / "ref"

WEIGHTS = (
    (40.0, 10.0, 15.0, 15.0, 5.0),
    (1.0, 13.5, 1.5, 1.5, 1.0),
    (1.0, 1.5, 15.0, 1.5, 1.0),
    (0.5, 1.0, 1.0, 10.0, 0.5),
    (1.0, 2.0, 2.0, 2.0, 10.0),
)
CONSTANT_LEVEL_1 = (102.5, 102.5, 102.5, 102.5, 20.5)
CONSTANT_LEVEL_99 = (102.0, 102.44, 102.43, 102.44, 20.45)


@dataclass(frozen=True)
class PredictionScenario:
    level: int
    rebirth: int
    efforts: tuple[int, ...]
    battle: tuple[int, ...]


@dataclass(frozen=True)
class PredictionCandidate:
    base: tuple[float, ...]
    base_wave: tuple[float, ...]
    source_points: tuple[int, ...]
    scenarios: tuple[PredictionScenario, ...]
    paths: int


@dataclass(frozen=True)
class PetPrediction:
    candidates: tuple[PredictionCandidate, ...]
    note: str

    @property
    def base(self) -> tuple[float, ...]:
        return self.candidates[0].base

    @property
    def scenarios(self) -> tuple[PredictionScenario, ...]:
        return self.candidates[0].scenarios

    @property
    def current_battle_delta(self) -> tuple[int, ...]:
        return (0, 0, 0, 0, 0)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True)
class PetBasis:
    key: str
    name: str
    server: str
    base: tuple[float, ...]
    growth: float


@lru_cache(maxsize=1)
def load_pet_bases() -> dict[tuple[str, str], PetBasis]:
    """Load the extracted pet basis database."""
    raw = json.loads((DATA_DIR / "pet_bases.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("宠物基数库格式错误")
    result: dict[tuple[str, str], PetBasis] = {}
    for item_key, item in raw.items():
        basis = PetBasis(
            key=str(item_key),
            name=str(item["name"]),
            server=str(item["server"]),
            base=tuple(float(value) for value in item["base"]),
            growth=float(item["growth"]),
        )
        key = (basis.server, basis.name)
        if key in result:
            raise ValueError(f"宠物基数库中存在重复宠物：{basis.name}")
        result[key] = basis
    return result


@lru_cache(maxsize=1)
def _egg_codes() -> tuple[int, ...]:
    raw = (DATA_DIR / "egg_codes.bin").read_bytes()
    if len(raw) % 4:
        raise ValueError("开蛋编码库数据长度错误")
    return tuple(value[0] for value in struct.iter_unpack("<I", raw))


@lru_cache(maxsize=1)
def _egg_path_map() -> dict[int, int]:
    mask = (1 << 28) - 1
    return {code & mask: code >> 28 for code in _egg_codes()}


def find_pet_basis(name: str, server: str) -> PetBasis:
    if server not in ("normal", "active"):
        raise ValueError("服务器类型必须是 normal 或 active")
    try:
        return load_pet_bases()[(server, name)]
    except KeyError as exc:
        server_label = "活动服" if server == "active" else "普通服"
        raise LookupError(f"基数库中没有{server_label}宠物“{name}”") from exc


def apply_basis_wave(
    original: Sequence[float], wave: Sequence[float]
) -> tuple[float, ...]:
    if len(original) != 5 or len(wave) != 5:
        raise ValueError("原始基数和波动必须各包含五项")
    values = tuple(float(value) for value in original)
    changes = tuple(float(value) for value in wave)
    if any(not isfinite(value) for value in values + changes):
        raise ValueError("基数和波动必须是有限数字")
    return tuple(max(0.0, round(values[index] + changes[index], 7)) for index in range(5))


def simulate_base(
    base: Sequence[float],
    any_level: int,
    allocations: Sequence[Sequence[int]] | None = None,
) -> tuple[PredictionScenario, ...]:
    """Return the four scenarios used by the manual basis simulator."""
    if len(base) != 5:
        raise ValueError("请填写完整的五项基数")
    normalized = tuple(float(value) for value in base)
    if any(not isfinite(value) or value < 0 for value in normalized):
        raise ValueError("五项基数必须是非负数字")
    if any_level not in range(1, 100):
        raise ValueError("任意等级必须在 1 到 99 之间")
    if allocations is None:
        allocations = (
            (0, 98, 0, 0, 0),
            (0, any_level - 1, 0, 0, 0),
            (0, 98, 0, 0, 0),
        )
    budgets = (98, any_level - 1, 98)
    if len(allocations) != 3:
        raise ValueError("自由加点必须包含三个场景")
    normalized_allocations: list[tuple[int, ...]] = []
    for points, budget in zip(allocations, budgets):
        if len(points) != 5:
            raise ValueError("每个场景必须填写五项自由加点")
        if any(not isinstance(value, int) for value in points):
            raise ValueError("自由加点必须是整数")
        normalized_points = tuple(points)
        if any(value < 0 for value in normalized_points):
            raise ValueError("自由加点不能是负数")
        if sum(normalized_points) > budget:
            raise ValueError(f"自由加点超过可分配点数（{budget}点）")
        normalized_allocations.append(normalized_points)
    return (
        scenario(normalized, 1, 0, (0, 0, 0, 0, 0)),
        scenario(normalized, 99, 0, normalized_allocations[0]),
        scenario(normalized, any_level, 0, normalized_allocations[1]),
        scenario(normalized, 99, 10, normalized_allocations[2]),
    )


def round_display(value: float) -> int:
    """Match the positive-number rounding used by the JavaScript calculator."""
    return floor(value + 0.5 + 1e-9)


def floor_to_five(value: float) -> int:
    return floor((value + 1e-8) / 5) * 5


def base_for_turn_tenths(base_tenths: Sequence[int], rebirth: int) -> tuple[int, ...]:
    if rebirth not in range(11):
        raise ValueError("转生次数必须在 0 到 10 之间")
    current = tuple(int(value) for value in base_tenths)
    for _ in range(rebirth):
        current = tuple(floor(value * 103 / 100) for value in current)
    return current


def scenario(
    base: Sequence[float],
    level: int,
    rebirth: int,
    points: Sequence[int],
) -> PredictionScenario:
    if level not in range(1, 100):
        raise ValueError("等级必须在 1 到 99 之间")
    if len(base) != 5 or len(points) != 5:
        raise ValueError("基数和加点必须各包含五项")

    base_tenths = tuple(round_display(float(value) * 10) for value in base)
    turned = base_for_turn_tenths(base_tenths, rebirth)
    factor = 0.2 + (level - 1) / 24
    precise = tuple(
        value / 10 * factor + int(points[index])
        for index, value in enumerate(turned)
    )
    efforts = tuple(round_display(value) for value in precise)
    constants = tuple(
        CONSTANT_LEVEL_1[index]
        + (CONSTANT_LEVEL_99[index] - CONSTANT_LEVEL_1[index])
        * (level - 1)
        / 98
        for index in range(5)
    )
    raw_battle = tuple(
        constants[row]
        + sum(WEIGHTS[row][column] * precise[column] for column in range(5))
        for row in range(5)
    )
    battle = tuple(
        floor(raw_battle[index] + 1e-8)
        if index == 4
        else floor_to_five(raw_battle[index])
        for index in range(5)
    )
    return PredictionScenario(level, rebirth, efforts, battle)


def predict_pet(
    pet: Mapping[str, object],
    basis: PetBasis,
    source_points: Sequence[int],
    target_growth: int | None = None,
) -> PetPrediction:
    """Match an arbitrary-level panel against V14's exact ten-value model."""
    level = int(pet["level"])
    rebirth = int(pet["reincarnation_degree"])
    applied = int(pet["attr_point_applied"])
    efforts = tuple(int(pet[key]) for key in EFFORT_KEYS)
    observed_battle = tuple(int(pet[key]) for key in BATTLE_KEYS)
    if level not in range(1, 100) or rebirth not in range(11):
        raise ValueError("宠物等级或转生次数超出支持范围")
    if applied < 0 or applied > level - 1:
        raise ValueError("宠物已分配自由点与当前等级不一致")
    if len(source_points) != 5 or any(
        not isinstance(value, int) for value in source_points
    ):
        raise ValueError("五项自由加点必须是整数")
    points = tuple(source_points)
    if any(value < 0 for value in points):
        raise ValueError("五项自由加点不能是负数")
    if sum(points) != applied:
        raise ValueError(f"五项自由加点合计必须等于当前已分配点数 {applied}")
    growth = round_display(basis.growth) if target_growth is None else target_growth
    if not isinstance(growth, int):
        raise ValueError("成长必须是整数")
    growth_min = max(0, round_display(basis.growth) - 40)
    growth_max = round_display(basis.growth) + 20
    if growth not in range(growth_min, growth_max + 1):
        raise ValueError(
            f"成长必须在基数库支持范围 {growth_min} 到 {growth_max} 之间"
        )
    base_efforts = tuple(efforts[index] - points[index] for index in range(5))
    if min(base_efforts) < 0:
        raise ValueError("自由加点不能大于当前对应努力值")
    outputs: dict[
        tuple[int, ...],
        tuple[
            int,
            tuple[float, ...],
            tuple[float, ...],
            tuple[int, ...],
            tuple[PredictionScenario, ...],
        ],
    ] = {}
    factor = 0.2 + (level - 1) / 24
    allowed_tenths = tuple(
        _allowed_base_tenths(
            basis.base[index], base_efforts[index], rebirth, factor
        )
        for index in range(5)
    )
    if any(not values for values in allowed_tenths):
        raise ValueError("当前努力值和自由加点无法对应此宠物的可能基数")

    for tenths, _candidate_growth, paths in _iter_all_basis_candidates(
        basis, growth, allowed_tenths
    ):
        base = tuple(value / 10 for value in tenths)
        base_wave = tuple(
            round(base[index] - basis.base[index], 7) for index in range(5)
        )
        current = scenario(base, level, rebirth, points)
        battle_matches = all(
            abs(calculated - observed) <= tolerance
            for calculated, observed, tolerance in zip(
                current.battle, observed_battle, BATTLE_TOLERANCES
            )
        )
        if current.efforts != efforts or not battle_matches:
            continue

        one = scenario(base, 1, 0, (0, 0, 0, 0, 0))
        ninety_nine = scenario(base, 99, 0, (0, 98, 0, 0, 0))
        ten_turn = scenario(base, 99, 10, (0, 98, 0, 0, 0))
        projected = (one, ninety_nine, ten_turn)
        key = tenths + points + tuple(
            value
            for item in projected
            for values in (item.efforts, item.battle)
            for value in values
        )
        previous = outputs.get(key)
        if previous is None:
            outputs[key] = (paths, base, base_wave, points, projected)
        else:
            outputs[key] = (
                previous[0] + paths,
                previous[1],
                previous[2],
                previous[3],
                previous[4],
            )

    if not outputs:
        raise ValueError(
            "基数库中未找到努力值精确一致且战斗值处于网页容差内的候选"
        )
    ranked = sorted(outputs.values(), key=lambda item: item[0], reverse=True)
    candidates = tuple(
        PredictionCandidate(base, base_wave, points, projected, paths)
        for paths, base, base_wave, points, projected in ranked
    )
    note = (
        f"已按成长 {growth} 和手动自由加点搜索，当前努力值精确一致，"
        "战斗值采用网页容差（前四项 +/-5、魔力 +/-1）；"
        f"共 {len(candidates)} 套候选，已全部列出"
    )
    return PetPrediction(candidates, note)


def _allowed_base_tenths(
    original: float,
    target_effort: int,
    rebirth: int,
    factor: float,
) -> frozenset[int]:
    """Reverse one effort value to the possible 0-turn tenth-unit bases."""
    possible: set[int] = set()
    maximum = max(0, ceil((original + 13) * 10))
    for tenth in range(maximum + 1):
        turned = tenth
        for _ in range(rebirth):
            turned = floor(turned * 103 / 100)
        if round_display(turned / 10 * factor) == target_effort:
            possible.add(tenth)
    return frozenset(possible)


def _iter_all_basis_candidates(
    basis: PetBasis,
    target_growth: int,
    allowed_tenths: Sequence[frozenset[int]],
) -> Iterator[tuple[tuple[int, ...], int, int]]:
    """Generate every candidate used by V14's unknown-growth search."""
    original = basis.base
    center_growth = basis.growth
    growth_min = max(0, round_display(center_growth) - 40)
    growth_max = round_display(center_growth) + 20
    base_fifths = tuple(round_display(value * 5) for value in original)
    egg_options: list[list[tuple[int, int]]] = []
    for index in range(4):
        options: list[tuple[int, int]] = []
        for delta in range(-64, 64):
            tenth = max(0, base_fifths[index] + delta) * 2
            if tenth in allowed_tenths[index]:
                options.append((delta, tenth))
        egg_options.append(options)
    path_map = _egg_path_map()
    for first, second, third, fourth in product(*egg_options):
        deltas = (first[0], second[0], third[0], fourth[0])
        packed = sum((delta + 64) << (7 * index) for index, delta in enumerate(deltas))
        paths = path_map.get(packed)
        if paths is None:
            continue
        fifth_delta = -sum(deltas)
        fifth_tenth = max(0, base_fifths[4] + fifth_delta) * 2
        if fifth_tenth not in allowed_tenths[4]:
            continue
        tenths = (first[1], second[1], third[1], fourth[1], fifth_tenth)
        growth = round_display(sum(tenths) / 10)
        if center_growth <= growth <= growth_max and growth == target_growth:
            yield tenths, growth, paths

    # Low-sum egg draws have no shared cap. Clipping at zero can raise their
    # displayed sum, so all valid integer growth levels remain searchable.
    low_options: list[list[tuple[int, int]]] = []
    for index in range(5):
        options: list[tuple[int, int]] = []
        for offset in range(-8, 9):
            tenth = round_display(max(0.0, original[index] + offset) * 10)
            if tenth in allowed_tenths[index]:
                options.append((offset, tenth))
        low_options.append(options)

    target_total = target_growth * 10
    for first, second, third, fourth in product(*low_options[:4]):
        partial_deltas = (first[0], second[0], third[0], fourth[0])
        partial_sum = sum(partial_deltas)
        if partial_sum >= 8:
            continue
        values4 = (first[1], second[1], third[1], fourth[1])
        subtotal = sum(values4)
        for fifth, tail in low_options[4]:
            if fifth > min(8, -partial_sum - 1):
                continue
            total = subtotal + tail
            if total != target_total:
                continue
            if total % 10:
                continue
            growth = total // 10
            if growth_min <= growth <= growth_max:
                yield values4 + (tail,), growth, 1
