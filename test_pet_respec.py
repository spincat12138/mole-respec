import sqlite3
import tempfile
import unittest
from pathlib import Path

from pet_respec import (
    RespecRunner,
    WashConfig,
    is_target_reached,
    target_fields,
)
from protocol_backend import ProtocolBackend
from pet_prediction import (
    PetBasis,
    apply_basis_wave,
    base_for_turn_tenths,
    find_pet_basis,
    predict_pet,
    scenario,
    simulate_base,
)


ITEM_IDS = (350013, 360008, 360038, 350014, 360009)


def make_pet(pet_id=7, growth=100, base=None, derived=None):
    base = base or [10, 20, 30, 40, 50]
    derived = derived or [100, 200, 30, 40, 50]
    return {
        "pet_id": pet_id,
        "grow_value": growth,
        "physique": base[0],
        "strength": base[1],
        "endurance": base[2],
        "quick": base[3],
        "intelligence": base[4],
        "hp_max": derived[0],
        "attack": derived[1],
        "defense": derived[2],
        "speed": derived[3],
        "mp_max": derived[4],
    }


class FakeBackend:
    def __init__(self, pets, quantity=5):
        self.pets = list(pets)
        self.recorded_results = []
        self.quantities = {item_id: 0 for item_id in ITEM_IDS}
        self.quantities[ITEM_IDS[2]] = quantity

    def load_pets(self):
        return [dict(pet) for pet in self.pets]

    def load_item_quantities(self):
        return dict(self.quantities)

    def wash_once(self, pet, item_index, quantities):
        self.pets[0]["grow_value"] += 1
        quantities[ITEM_IDS[item_index]] -= 1

    def record_wash_result(self, pet, item_index):
        self.recorded_results.append((dict(pet), item_index))


class FakeMoer:
    _XD_ITEM_TYPES = ("绿色成长", "红色成长", "大丸子", "紫色五项", "红色五项")

    def __init__(self):
        self.requested_ids = []
        self.moves = []
        self.recorded_results = []

    def _get_pet_store(self, _socket, _uid_bytes):
        return {"pets": [{"pet_id": 11}, {"pet_id": 12}]}

    def _get_pet_info(self, _socket, _uid_bytes, pet_id):
        self.requested_ids.append(pet_id)
        pet = make_pet(pet_id=pet_id)
        pet["reincarnation_degree"] = pet_id - 10
        return pet

    def _pet_back_store(self, _socket, _uid_bytes, pet):
        self.moves.append(("store", pet["pet_id"]))

    def _pet_back_bag(self, _socket, _uid_bytes, pet):
        self.moves.append(("bag", pet["pet_id"]))

    def _record_pet_result(
        self,
        pet,
        operation,
        uid,
        fwq,
        item_type,
        database_path,
    ):
        self.recorded_results.append(
            (dict(pet), operation, uid, fwq, item_type, database_path)
        )


class TargetTests(unittest.TestCase):
    def test_big_pill_base_includes_growth_and_five_stats(self):
        fields = target_fields(2, "base")
        self.assertEqual([field.key for field in fields], [
            "grow_value",
            "physique",
            "strength",
            "endurance",
            "quick",
            "intelligence",
        ])
        comparisons = {field.key: field.comparison for field in fields}
        self.assertEqual(comparisons["grow_value"], "min")
        self.assertEqual(comparisons["physique"], "min")
        self.assertEqual(comparisons["strength"], "min")
        self.assertEqual(comparisons["endurance"], "max")
        self.assertEqual(comparisons["quick"], "min")
        self.assertEqual(comparisons["intelligence"], "max")

    def test_base_endurance_and_intelligence_are_upper_bounds(self):
        pet = make_pet(base=[10, 20, 30, 40, 50])
        self.assertTrue(is_target_reached(pet, 3, "base", [10, 20, 30, 40, 50]))
        self.assertFalse(is_target_reached(pet, 3, "base", [10, 20, 29, 40, 50]))
        self.assertFalse(is_target_reached(pet, 3, "base", [10, 20, 30, 40, 49]))

    def test_derived_defense_and_magic_are_upper_bounds(self):
        fields = target_fields(3, "derived")
        comparisons = {field.key: field.comparison for field in fields}
        self.assertEqual(comparisons["defense"], "max")
        self.assertEqual(comparisons["mp_max"], "max")
        self.assertEqual(comparisons["attack"], "min")

    def test_zero_target_is_ignored(self):
        pet = make_pet(derived=[100, 200, 30, 40, 50])
        self.assertTrue(is_target_reached(pet, 3, "derived", [100, 0, 30, 40, 0]))
        self.assertFalse(is_target_reached(pet, 3, "derived", [101, 0, 30, 40, 0]))

    def test_growth_items_only_use_growth_target(self):
        for item_index in (0, 1):
            fields = target_fields(item_index, "derived")
            self.assertEqual([(field.key, field.comparison) for field in fields], [
                ("grow_value", "min")
            ])

    def test_config_requires_at_least_one_target(self):
        with self.assertRaisesRegex(ValueError, "至少填写"):
            WashConfig(7, 3, "base", (0, 0, 0, 0, 0), 10).validate()


class RunnerTests(unittest.TestCase):
    def test_runner_washes_once_when_initial_pet_already_reaches_target(self):
        backend = FakeBackend([make_pet(growth=100)], quantity=5)
        progress = []

        result = RespecRunner(backend, ITEM_IDS).run(
            WashConfig(7, 2, "base", (100, 0, 0, 0, 0, 0), 5),
            lambda: False,
            progress.append,
        )

        self.assertEqual(result.reason, "target")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(progress), 1)
        self.assertEqual(result.remaining_items, 4)
        self.assertEqual(backend.recorded_results[0][0]["grow_value"], 101)

    def test_runner_stops_when_target_is_reached(self):
        backend = FakeBackend([make_pet(growth=100)], quantity=5)
        progress = []
        result = RespecRunner(backend, ITEM_IDS).run(
            WashConfig(7, 2, "base", (102, 0, 0, 0, 0, 0), 5),
            lambda: False,
            progress.append,
        )
        self.assertEqual(result.reason, "target")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(progress), 2)
        self.assertEqual(result.remaining_items, 3)
        self.assertEqual(
            [(pet["grow_value"], item_index) for pet, item_index in backend.recorded_results],
            [(101, 2), (102, 2)],
        )

    def test_runner_stops_before_using_missing_inventory(self):
        backend = FakeBackend([make_pet(growth=100)], quantity=1)
        result = RespecRunner(backend, ITEM_IDS).run(
            WashConfig(7, 2, "base", (105, 0, 0, 0, 0, 0), 5),
            lambda: False,
            lambda _progress: None,
        )
        self.assertEqual(result.reason, "inventory")
        self.assertEqual(result.attempts, 1)

    def test_runner_honors_stop_before_next_request(self):
        backend = FakeBackend([make_pet(growth=100)], quantity=5)
        result = RespecRunner(backend, ITEM_IDS).run(
            WashConfig(7, 2, "base", (105, 0, 0, 0, 0, 0), 5),
            lambda: True,
            lambda _progress: None,
        )
        self.assertEqual(result.reason, "stopped")
        self.assertEqual(result.attempts, 0)


class ProtocolBackendTests(unittest.TestCase):
    @staticmethod
    def make_backend():
        backend = ProtocolBackend.__new__(ProtocolBackend)
        backend._moer = FakeMoer()
        backend._result_database_path = ProtocolBackend._application_dir() / "pet_results.db"
        backend._game_socket = object()
        backend._uid_bytes = [0, 0, 0, 1]
        backend.server = 6
        backend.uid = 123456
        return backend

    def test_storage_list_does_not_fetch_details(self):
        backend = self.make_backend()

        pets = backend.load_storage_pets()

        self.assertEqual(backend._moer.requested_ids, [])
        self.assertEqual([pet["pet_id"] for pet in pets], [11, 12])

    def test_pet_info_fetches_one_selected_storage_pet(self):
        backend = self.make_backend()

        pet = backend.load_pet_info(12)

        self.assertEqual(backend._moer.requested_ids, [12])
        self.assertEqual(pet["pet_id"], 12)
        self.assertIn("reincarnation_degree", pet)

    def test_pet_moves_use_store_and_bag_protocol_helpers(self):
        backend = self.make_backend()
        pet = {"pet_id": 12}

        backend.move_pet_to_store(pet)
        backend.move_pet_to_bag(pet)

        self.assertEqual(backend._moer.moves, [("store", 12), ("bag", 12)])

    def test_wash_result_records_account_server_item_and_database(self):
        backend = self.make_backend()
        pet = make_pet()

        backend.record_wash_result(pet, 2)

        self.assertEqual(len(backend._moer.recorded_results), 1)
        recorded = backend._moer.recorded_results[0]
        self.assertEqual(recorded[:5], (pet, "洗宠", 123456, 6, "大丸子"))
        self.assertEqual(
            recorded[5], ProtocolBackend._application_dir() / "pet_results.db"
        )

    def test_wash_result_is_persisted_to_sqlite(self):
        backend = ProtocolBackend()
        backend.uid = 123456
        backend.server = 6
        pet = make_pet(growth=197)
        pet.update({"type_id": 1, "nick": "test-pet", "level": 99})

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "pet_results.db"
            backend._result_database_path = database_path
            backend.record_wash_result(pet, 2)

            connection = sqlite3.connect(database_path)
            try:
                row = connection.execute(
                    """
                    SELECT operation, uid, fwq, item_type, pet_level,
                           nickname, growth
                    FROM pet_results
                    """
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(
            row,
            ("洗宠", 123456, 6, "大丸子", 99, "test-pet", 197),
        )


class PetPredictionTests(unittest.TestCase):
    def test_rebirth_uses_three_percent_floor_each_turn(self):
        self.assertEqual(base_for_turn_tenths((260, 660), 2), (275, 699))

    def test_level_one_scenario_matches_index_formula(self):
        result = scenario((26, 66, 28, 68, 9), 1, 0, (0, 0, 0, 0, 0))

        self.assertEqual(result.efforts, (5, 13, 6, 14, 2))
        self.assertEqual(result.battle, (735, 315, 230, 260, 108))

    def test_prediction_recovers_forward_generated_panels(self):
        base = (26, 66, 28, 68, 9)
        current = scenario(base, 50, 3, (0, 49, 0, 0, 0))
        basis = PetBasis("test", "测试宠物", "normal", base, 197)
        pet = {
            "type_id": 1,
            "nick": "测试宠物",
            "level": 50,
            "reincarnation_degree": 3,
            "grow_value": 197,
            "attr_point_applied": 49,
            **dict(zip(("physique", "strength", "endurance", "quick", "intelligence"), current.efforts)),
            **dict(zip(("hp_max", "attack", "defense", "speed", "mp_max"), current.battle)),
        }

        prediction = predict_pet(pet, basis, (0, 49, 0, 0, 0))

        expected = (
            scenario(base, 1, 0, (0, 0, 0, 0, 0)),
            scenario(base, 99, 0, (0, 98, 0, 0, 0)),
            scenario(base, 99, 10, (0, 98, 0, 0, 0)),
        )
        self.assertIn(expected, [candidate.scenarios for candidate in prediction.candidates])
        self.assertEqual(prediction.current_battle_delta, (0, 0, 0, 0, 0))
        self.assertIn("十项", prediction.note)

    def test_prediction_recovers_non_strength_point_distribution(self):
        base = (26, 66, 28, 68, 9)
        points = (10, 15, 8, 12, 4)
        current = scenario(base, 50, 6, points)
        basis = PetBasis("test", "测试宠物", "normal", base, 197)
        pet = {
            "type_id": 1,
            "nick": "测试宠物",
            "level": 50,
            "reincarnation_degree": 6,
            "grow_value": 197,
            "attr_point_applied": sum(points),
            **dict(zip(("physique", "strength", "endurance", "quick", "intelligence"), current.efforts)),
            **dict(zip(("hp_max", "attack", "defense", "speed", "mp_max"), current.battle)),
        }

        prediction = predict_pet(pet, basis, points)

        self.assertIn(base, [candidate.base for candidate in prediction.candidates])
        self.assertEqual(prediction.current_battle_delta, (0, 0, 0, 0, 0))
        self.assertTrue(
            all(candidate.source_points == points for candidate in prediction.candidates)
        )
        with self.assertRaisesRegex(ValueError, "合计必须等于"):
            predict_pet(pet, basis, (0, 0, 0, 0, 0))

    def test_prediction_defaults_to_basis_growth_when_omitted(self):
        base = (26, 66, 28, 68, 9)
        points = (10, 15, 8, 12, 4)
        current = scenario(base, 50, 6, points)
        basis = PetBasis("test", "测试宠物", "normal", base, 197)
        pet = {
            "type_id": 1,
            "nick": "测试宠物",
            "level": 50,
            "reincarnation_degree": 6,
            "grow_value": 188,
            "attr_point_applied": sum(points),
            **dict(zip(("physique", "strength", "endurance", "quick", "intelligence"), current.efforts)),
            **dict(zip(("hp_max", "attack", "defense", "speed", "mp_max"), current.battle)),
        }

        prediction = predict_pet(pet, basis, points)

        self.assertIn(base, [candidate.base for candidate in prediction.candidates])

    def test_prediction_accepts_manual_non_center_growth(self):
        original = (26, 66, 28, 68, 9)
        washed = (25, 65, 27, 67, 8)
        points = (10, 15, 8, 12, 4)
        current = scenario(washed, 50, 6, points)
        basis = PetBasis("test", "测试宠物", "normal", original, 197)
        pet = {
            "type_id": 1,
            "nick": "测试宠物",
            "level": 50,
            "reincarnation_degree": 6,
            "attr_point_applied": sum(points),
            **dict(zip(("physique", "strength", "endurance", "quick", "intelligence"), current.efforts)),
            **dict(zip(("hp_max", "attack", "defense", "speed", "mp_max"), current.battle)),
        }

        prediction = predict_pet(pet, basis, points, 192)

        self.assertIn(washed, [candidate.base for candidate in prediction.candidates])

    def test_prediction_returns_every_exact_candidate(self):
        base = (26, 66, 28, 68, 9)
        points = (7, 18, 6, 13, 5)
        current = scenario(base, 50, 4, points)
        basis = PetBasis("test", "测试宠物", "normal", base, 197)
        pet = {
            "type_id": 1,
            "nick": "测试宠物",
            "level": 50,
            "reincarnation_degree": 4,
            "grow_value": 197,
            "attr_point_applied": sum(points),
            **dict(zip(("physique", "strength", "endurance", "quick", "intelligence"), current.efforts)),
            **dict(zip(("hp_max", "attack", "defense", "speed", "mp_max"), current.battle)),
        }

        prediction = predict_pet(pet, basis, points)

        self.assertGreaterEqual(prediction.candidate_count, 1)
        self.assertEqual(prediction.candidate_count, len(prediction.candidates))
        self.assertTrue(all(len(candidate.scenarios) == 3 for candidate in prediction.candidates))
        self.assertTrue(
            all(candidate.source_points == points for candidate in prediction.candidates)
        )
        self.assertIn("已全部列出", prediction.note)

    def test_prediction_rejects_panel_without_exact_ten_value_match(self):
        base = (26, 66, 28, 68, 9)
        current = scenario(base, 50, 0, (0, 49, 0, 0, 0))
        basis = PetBasis("test", "测试宠物", "normal", base, 197)
        battle = list(current.battle)
        battle[0] += 1
        pet = {
            "type_id": 1,
            "nick": "测试宠物",
            "level": 50,
            "reincarnation_degree": 0,
            "grow_value": 197,
            "attr_point_applied": 49,
            **dict(zip(("physique", "strength", "endurance", "quick", "intelligence"), current.efforts)),
            **dict(zip(("hp_max", "attack", "defense", "speed", "mp_max"), battle)),
        }

        with self.assertRaisesRegex(ValueError, "十项完全一致"):
            predict_pet(pet, basis, (0, 49, 0, 0, 0))

    def test_base_simulation_returns_requested_four_scenarios(self):
        base = (26, 66, 28, 68, 9)

        results = simulate_base(base, 37)

        self.assertEqual(
            [(item.rebirth, item.level) for item in results],
            [(0, 1), (0, 99), (0, 37), (10, 99)],
        )
        self.assertEqual(results[2], scenario(base, 37, 0, (0, 36, 0, 0, 0)))

    def test_base_simulation_rejects_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "非负"):
            simulate_base((26, -1, 28, 68, 9), 50)
        with self.assertRaisesRegex(ValueError, "1 到 99"):
            simulate_base((26, 66, 28, 68, 9), 100)

    def test_basis_database_uses_pet_and_server_from_index(self):
        active = find_pet_basis("雪绒草", "active")
        normal = find_pet_basis("雪绒草", "normal")

        self.assertEqual(active.base, (26, 66, 28, 68, 9))
        self.assertEqual(active.growth, 197)
        self.assertEqual(normal.base, (18, 39, 22, 33, 8))
        self.assertEqual(normal.growth, 120)

    def test_wave_and_custom_allocations_drive_simulation(self):
        target = apply_basis_wave((26, 66, 28, 68, 9), (1, -2, 0.5, 0, -20))
        allocations = (
            (20, 30, 10, 18, 20),
            (10, 12, 5, 10, 12),
            (0, 0, 0, 0, 0),
        )

        results = simulate_base(target, 50, allocations)

        self.assertEqual(target, (27, 64, 28.5, 68, 0))
        self.assertEqual(results[1], scenario(target, 99, 0, allocations[0]))
        self.assertEqual(results[2], scenario(target, 50, 0, allocations[1]))
        self.assertEqual(results[3], scenario(target, 99, 10, allocations[2]))

    def test_allocations_cannot_exceed_scenario_budget(self):
        with self.assertRaisesRegex(ValueError, "49点"):
            simulate_base(
                (26, 66, 28, 68, 9),
                50,
                ((0, 98, 0, 0, 0), (0, 50, 0, 0, 0), (0, 98, 0, 0, 0)),
            )


if __name__ == "__main__":
    unittest.main()
