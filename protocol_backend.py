"""Adapter between the GUI workflow and the protocol code in ref/moer.py."""

from __future__ import annotations

import hashlib
import importlib
import socket
import sys
from pathlib import Path
from types import ModuleType
from typing import Mapping

from pet_respec import is_pet_washable


GIANT_ROCK_FRAGMENT_ID = 290011
EXPERIENCE_BOOK_ID = 350050
MISC_ITEM_IDS = (GIANT_ROCK_FRAGMENT_ID, EXPERIENCE_BOOK_ID)


class ProtocolBackend:
    def __init__(self) -> None:
        self._moer = self._load_moer()
        self._result_database_path = self._application_dir() / "pet_results.db"
        self._login_socket: socket.socket | None = None
        self._game_socket: socket.socket | None = None
        self._uid_bytes: list[int] | None = None
        self.server = 0
        self.uid = 0

    @staticmethod
    def _load_moer() -> ModuleType:
        ref_dir = Path(__file__).resolve().parent / "ref"
        if not ref_dir.exists():
            raise RuntimeError("缺少 ref 目录，无法加载协议实现")
        ref_path = str(ref_dir)
        if ref_path not in sys.path:
            sys.path.insert(0, ref_path)
        return importlib.import_module("moer")

    @staticmethod
    def _application_dir() -> Path:
        compiled = globals().get("__compiled__")
        if compiled is not None:
            containing_dir = getattr(compiled, "containing_dir", None)
            if containing_dir:
                return Path(containing_dir).resolve()
            return Path(sys.argv[0]).resolve().parent
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent

    @property
    def item_ids(self) -> tuple[int, ...]:
        return tuple(self._moer._XD_ITEM_IDS)

    @property
    def pet_egg_items(self) -> tuple[tuple[int, str], ...]:
        return tuple(self._moer._get_pet_egg_items())

    def pet_type_name(self, type_id: int) -> str:
        return self._moer._get_pet_name(type_id)

    def connect(self, uid: int, password: str, server: int) -> int:
        if self._game_socket is not None:
            raise RuntimeError("当前已有连接，请先断开")
        if uid <= 0:
            raise ValueError("米米号必须是正整数")
        if not password:
            raise ValueError("请输入密码")
        if server not in range(21):
            raise ValueError("服务器必须在 0 到 20 之间")

        # get_account() hashes the plain text once before login_taomi() hashes it again.
        account_password = hashlib.md5(password.encode("utf-8")).hexdigest()
        result = self._moer.login_taomi(
            uid, account_password, model=1, fwq=server, return_server=True
        )
        if result is None:
            raise ConnectionError("登录失败，请检查账号、密码和服务器")
        login_socket, game_socket, uid_bytes, actual_server = result
        game_socket.settimeout(12)
        self._login_socket = login_socket
        self._game_socket = game_socket
        self._uid_bytes = uid_bytes
        self.server = actual_server
        self.uid = uid
        return actual_server

    def close(self) -> None:
        sockets = (self._game_socket, self._login_socket)
        self._game_socket = None
        self._login_socket = None
        self._uid_bytes = None
        self.server = 0
        self.uid = 0
        for sock in sockets:
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def load_pets(self) -> list[dict[str, object]]:
        game_socket, uid_bytes = self._session()
        return self._moer._load_xd_pets(game_socket, uid_bytes)["pets"]

    def load_storage_pets(self) -> list[dict[str, object]]:
        game_socket, uid_bytes = self._session()
        return self._moer._get_pet_store(game_socket, uid_bytes)["pets"]

    def load_pet_info(self, pet_id: int) -> dict[str, object]:
        game_socket, uid_bytes = self._session()
        return self._moer._get_pet_info(game_socket, uid_bytes, int(pet_id))

    def move_pet_to_store(self, pet: Mapping[str, object]) -> None:
        game_socket, uid_bytes = self._session()
        self._moer._pet_back_store(game_socket, uid_bytes, pet)

    def move_pet_to_bag(self, pet: Mapping[str, object]) -> None:
        game_socket, uid_bytes = self._session()
        self._moer._pet_back_bag(game_socket, uid_bytes, pet)

    def release_pet(self, pet: Mapping[str, object]) -> None:
        game_socket, uid_bytes = self._session()
        self._moer._pet_release(game_socket, uid_bytes, pet)

    def load_item_quantities(self) -> dict[int, int]:
        game_socket, uid_bytes = self._session()
        return self._moer._get_xd_item_quantities(game_socket, uid_bytes)

    def load_misc_item_quantities(self) -> dict[int, dict[str, int]]:
        game_socket, uid_bytes = self._session()
        bag_items, _ = self._moer._get_prop_bag_info(game_socket, uid_bytes)
        store_items, _ = self._moer._get_prop_store_info(game_socket, uid_bytes)
        quantities = {
            item_id: {"bag": 0, "store": 0}
            for item_id in MISC_ITEM_IDS
        }
        for location, items in (("bag", bag_items), ("store", store_items)):
            for item in items:
                item_id = int(item["item_id"])
                if item_id in quantities:
                    quantities[item_id][location] += int(item["quantity"])
        return quantities

    def exchange_big_pills(self, count: int) -> None:
        self._validate_positive_count(count)
        game_socket, uid_bytes = self._session()
        self._moer.exchangelb(game_socket, uid_bytes, 3, count)

    def use_experience_books(self, count: int) -> None:
        self._validate_positive_count(count)
        game_socket, uid_bytes = self._session()
        self._moer.openbook(game_socket, uid_bytes, count)

    def load_exp_tree_experience(self) -> int:
        game_socket, uid_bytes = self._session()
        return int(self._moer._get_exp_tree_experience(game_socket, uid_bytes))

    def allocate_pet_experience(self, pet_id: int, experience: int) -> None:
        self._validate_positive_count(experience)
        game_socket, uid_bytes = self._session()
        self._moer._allocate_pet_experience(
            game_socket, uid_bytes, int(pet_id), experience
        )

    def upgrade_pet_to_zero_99(self, pet_id: int) -> None:
        game_socket, uid_bytes = self._session()
        self._moer._upgrade_pet_to_zero_99(game_socket, uid_bytes, int(pet_id))

    def upgrade_pet_to_ten_99(self, pet_id: int) -> None:
        game_socket, uid_bytes = self._session()
        self._moer._upgrade_pet_to_ten_99(game_socket, uid_bytes, int(pet_id))

    def open_pet_eggs(self, item_id: int, count: int) -> None:
        self._validate_positive_count(count)
        game_socket, uid_bytes = self._session()
        self._moer._open_pet_eggs(game_socket, uid_bytes, int(item_id), count)

    def wash_once(
        self,
        pet: Mapping[str, object],
        item_index: int,
        quantities: dict[int, int],
    ) -> None:
        if not is_pet_washable(pet):
            raise ValueError("只有0转宠物可以洗点")
        game_socket, uid_bytes = self._session()
        self._moer.eatwz(game_socket, uid_bytes, pet, item_index, quantities)

    def record_wash_result(
        self,
        pet: Mapping[str, object],
        item_index: int,
    ) -> None:
        if item_index not in range(len(self._moer._XD_ITEM_TYPES)):
            raise ValueError("未知洗点道具")
        self._moer._record_pet_result(
            pet,
            "洗宠",
            self.uid,
            self.server,
            self._moer._XD_ITEM_TYPES[item_index],
            database_path=self._result_database_path,
        )

    def _session(self) -> tuple[socket.socket, list[int]]:
        if self._game_socket is None or self._uid_bytes is None:
            raise RuntimeError("尚未连接游戏服务器")
        return self._game_socket, self._uid_bytes

    @staticmethod
    def _validate_positive_count(count: int) -> None:
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("数量必须是正整数")
