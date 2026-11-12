"""Core models and workflow for pet respec operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence


ITEM_TYPES = (
    "绿色成长",
    "红色成长",
    "大丸子",
    "紫色五项",
    "红色五项",
)

BASE_FIELDS = (
    ("physique", "体力"),
    ("strength", "力量"),
    ("endurance", "耐力"),
    ("quick", "敏捷"),
    ("intelligence", "智力"),
)

DERIVED_FIELDS = (
    ("hp_max", "生命"),
    ("attack", "攻击"),
    ("defense", "防御"),
    ("speed", "速度"),
    ("mp_max", "魔力"),
)


@dataclass(frozen=True)
class TargetField:
    key: str
    label: str
    comparison: str

    @property
    def comparison_label(self) -> str:
        return "不低于" if self.comparison == "min" else "不高于"


@dataclass(frozen=True)
class WashConfig:
    pet_id: int
    item_index: int
    mode: str
    targets: tuple[int, ...] | None
    max_attempts: int
    alternative_targets: tuple[tuple[int, ...] | None, ...] = ()

    @property
    def target_groups(self) -> tuple[tuple[int, ...], ...]:
        primary = (self.targets,) if self.targets is not None else ()
        alternatives = tuple(
            targets
            for targets in self.alternative_targets
            if targets is not None
        )
        return primary + alternatives

    @property
    def target_slots(self) -> tuple[tuple[int, ...] | None, ...]:
        padding = (None,) * (2 - len(self.alternative_targets))
        return (self.targets, *self.alternative_targets, *padding)

    def validate(self) -> None:
        if self.item_index not in range(len(ITEM_TYPES)):
            raise ValueError("请选择有效的洗点道具")
        if self.mode not in ("base", "derived"):
            raise ValueError("请选择有效的洗点方式")
        fields = target_fields(self.item_index, self.mode)
        if len(self.alternative_targets) > 2:
            raise ValueError("最多只能启用三组候选条件")
        if not self.target_groups:
            raise ValueError("至少选择一组候选条件")
        for index, targets in enumerate(self.target_slots, start=1):
            if targets is None:
                continue
            if len(targets) != len(fields):
                raise ValueError(f"候选 {index} 的期望数值数量与当前洗点方式不一致")
            if any(not isinstance(value, int) or value < 0 for value in targets):
                raise ValueError(f"候选 {index} 的期望数值必须是非负整数")
            if not any(targets):
                raise ValueError(f"候选 {index} 至少填写一个大于 0 的期望数值")
        if not isinstance(self.max_attempts, int) or self.max_attempts <= 0:
            raise ValueError("最多洗点次数必须是正整数")


@dataclass(frozen=True)
class WashProgress:
    attempt: int
    pet: Mapping[str, object]
    reached: bool
    remaining_items: int


@dataclass(frozen=True)
class WashResult:
    reason: str
    attempts: int
    initial_pet: Mapping[str, object]
    final_pet: Mapping[str, object]
    remaining_items: int

    @property
    def reached(self) -> bool:
        return self.reason == "target"


class RespecBackend(Protocol):
    def load_pets(self) -> list[dict[str, object]]: ...

    def load_item_quantities(self) -> dict[int, int]: ...

    def wash_once(
        self,
        pet: Mapping[str, object],
        item_index: int,
        quantities: dict[int, int],
    ) -> None: ...

    def record_wash_result(
        self,
        pet: Mapping[str, object],
        item_index: int,
    ) -> None: ...


def target_fields(item_index: int, mode: str) -> tuple[TargetField, ...]:
    """Return the target schema used by moer.py's xd workflow."""
    if item_index not in range(len(ITEM_TYPES)):
        raise ValueError("未知洗点道具")
    if mode not in ("base", "derived"):
        raise ValueError("未知洗点方式")

    if item_index in (0, 1):
        return (TargetField("grow_value", "成长", "min"),)

    source = BASE_FIELDS if mode == "base" else DERIVED_FIELDS
    fields = []
    if item_index == 2:
        fields.append(TargetField("grow_value", "成长", "min"))
    for key, label in source:
        comparison = "min"
        if mode == "base" and key in ("endurance", "intelligence"):
            comparison = "max"
        elif mode == "derived" and key in ("defense", "mp_max"):
            comparison = "max"
        fields.append(TargetField(key, label, comparison))
    return tuple(fields)


def is_target_reached(
    pet: Mapping[str, object],
    item_index: int,
    mode: str,
    targets: Sequence[int],
) -> bool:
    fields = target_fields(item_index, mode)
    if len(fields) != len(targets):
        raise ValueError("期望数值数量与当前洗点方式不一致")
    for field, target in zip(fields, targets):
        if target == 0:
            continue
        current = int(pet[field.key])
        if field.comparison == "min" and current < target:
            return False
        if field.comparison == "max" and current > target:
            return False
    return True


def is_any_target_reached(
    pet: Mapping[str, object],
    item_index: int,
    mode: str,
    target_groups: Sequence[Sequence[int]],
) -> bool:
    if not target_groups:
        raise ValueError("至少需要一组候选条件")
    return any(
        is_target_reached(pet, item_index, mode, targets)
        for targets in target_groups
    )


class RespecRunner:
    """Execute a bounded respec loop against a connected backend."""

    def __init__(self, backend: RespecBackend, item_ids: Sequence[int]) -> None:
        self._backend = backend
        self._item_ids = tuple(item_ids)

    def run(
        self,
        config: WashConfig,
        should_stop: Callable[[], bool],
        on_progress: Callable[[WashProgress], None],
    ) -> WashResult:
        config.validate()
        if len(self._item_ids) != len(ITEM_TYPES):
            raise ValueError("洗点道具配置不完整")

        pets = self._backend.load_pets()
        current = self._find_pet(pets, config.pet_id)
        initial = dict(current)
        quantities = self._backend.load_item_quantities()
        item_id = self._item_ids[config.item_index]

        attempts = 0
        reason = "limit"
        while attempts < config.max_attempts:
            if should_stop():
                reason = "stopped"
                break
            if quantities.get(item_id, 0) <= 0:
                reason = "inventory"
                break

            self._backend.wash_once(current, config.item_index, quantities)
            attempts += 1
            current = self._find_pet(self._backend.load_pets(), config.pet_id)
            self._backend.record_wash_result(current, config.item_index)
            reached = is_any_target_reached(
                current, config.item_index, config.mode, config.target_groups
            )
            on_progress(
                WashProgress(
                    attempt=attempts,
                    pet=dict(current),
                    reached=reached,
                    remaining_items=quantities.get(item_id, 0),
                )
            )
            if reached:
                reason = "target"
                break

        return WashResult(
            reason,
            attempts,
            initial,
            dict(current),
            quantities.get(item_id, 0),
        )

    @staticmethod
    def _find_pet(
        pets: Sequence[Mapping[str, object]], pet_id: int
    ) -> Mapping[str, object]:
        for pet in pets:
            if int(pet["pet_id"]) == pet_id:
                return pet
        raise LookupError("洗点宠物已不在宠物背包中")
