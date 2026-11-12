"""Adapter between the GUI workflow and the protocol code in ref/moer.py."""

from __future__ import annotations

import hashlib
import importlib
import socket
import sys
from pathlib import Path
from types import ModuleType
from typing import Mapping


class ProtocolBackend:
    def __init__(self) -> None:
        self._moer = self._load_moer()
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

    @property
    def item_ids(self) -> tuple[int, ...]:
        return tuple(self._moer._XD_ITEM_IDS)

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

    def load_item_quantities(self) -> dict[int, int]:
        game_socket, uid_bytes = self._session()
        return self._moer._get_xd_item_quantities(game_socket, uid_bytes)

    def wash_once(
        self,
        pet: Mapping[str, object],
        item_index: int,
        quantities: dict[int, int],
    ) -> None:
        game_socket, uid_bytes = self._session()
        self._moer.eatwz(game_socket, uid_bytes, pet, item_index, quantities)

    def _session(self) -> tuple[socket.socket, list[int]]:
        if self._game_socket is None or self._uid_bytes is None:
            raise RuntimeError("尚未连接游戏服务器")
        return self._game_socket, self._uid_bytes
