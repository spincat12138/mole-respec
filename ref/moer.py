import time, struct, random, socket, hashlib, threading, weakref
import sqlite3
import sys
from collections import deque
from contextlib import closing
from datetime import datetime
from pathlib import Path

from item_names import ITEM_NAMES as _ITEM_NAMES
from pet_names import PET_NAMES as _PET_NAMES

_PROFESSION_NAMES = {
    0: '无',
    1: '剑士',
    2: '弓箭手',
    3: '魔法师',
    4: '传教士',
    5: '忍者',
    6: '狂战士',
    7: '黑魔导士',
    8: '圣言使',
    9: '巫术士',
}
_TMF_HEADER_SIZE = 18
_PET_ALLOCATE_ATTR_POINTS_COMMAND = 1606
_PET_RESET_ATTR_POINTS_COMMAND = 1565
_PET_RESET_ATTR_POINTS_ITEM_ID = 360010
_PET_LEARN_SKILL_COMMAND = 1601
_PET_RELEASE_COMMAND = 1561
_EXP_TREE_COMMAND = 2000
_PET_AUTO_ALLOCATE_ATTR_POINTS_COMMAND = 1603
_MAX_EXP_PER_PACKET = 999999
_EXPERIENCE_BOOK_ID = 350050
_EXPERIENCE_PER_BOOK = 99999
_ZERO_99_TARGET_EXP = 60796450
_OPEN_PET_EGG_COMMAND = 1130
_ATTR_NAMES = ('体力', '力量', '耐力', '敏捷', '智力')
_XD_ITEM_TYPES = ('绿色成长', '红色成长', '大丸子', '紫色五项', '红色五项')
_XD_ITEM_IDS = (350013, 360008, 360038, 350014, 360009)
_PET_EGG_ITEMS = tuple(
    sorted(
        (item_id, name)
        for item_id, name in _ITEM_NAMES.items()
        if name.endswith(('精灵蛋', '精品蛋'))
    )
)
_PET_POSITION_NAMES = {
    1: '宠物背包',
    2: '待命',
    3: '主战',
    4: '辅助',
}


def _get_item_name(item_id):
    """返回 Item ID 对应的中文名，并明确标记资料中缺失的 ID。"""
    return _ITEM_NAMES.get(item_id, '未知物品（Item ID：%d）' % item_id)


def _get_pet_name(type_id):
    """返回精灵类型名称；未知类型保留 ID 便于补充映射。"""
    return _PET_NAMES.get(type_id, '未知精灵_%d' % type_id)


def _get_pet_egg_items():
    return _PET_EGG_ITEMS


def _get_application_dir():
    """返回源码目录；打包运行时返回可执行文件所在目录。"""
    compiled = globals().get('__compiled__')
    if compiled is not None:
        containing_dir = getattr(compiled, 'containing_dir', None)
        if containing_dir:
            return Path(containing_dir).resolve()
        return Path(sys.argv[0]).resolve().parent
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _record_pet_result(
        pet, operation, uid, fwq, item_type=None, database_path=None):
    """将一次洗宠或开蛋结果写入 SQLite 数据库。"""
    if database_path is None:
        database_path = _get_application_dir() / 'pet_results.db'
    else:
        database_path = Path(database_path)
    with closing(sqlite3.connect(database_path, timeout=30)) as connection:
        with connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS pet_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    uid INTEGER,
                    fwq INTEGER,
                    item_type TEXT,
                    pet_level INTEGER,
                    pet_name TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    growth INTEGER NOT NULL,
                    physique INTEGER NOT NULL,
                    hp INTEGER NOT NULL,
                    strength INTEGER NOT NULL,
                    attack INTEGER NOT NULL,
                    endurance INTEGER NOT NULL,
                    defense INTEGER NOT NULL,
                    agility INTEGER NOT NULL,
                    speed INTEGER NOT NULL,
                    intelligence INTEGER NOT NULL,
                    magic INTEGER NOT NULL
                )
            ''')
            columns = {
                row[1]
                for row in connection.execute('PRAGMA table_info(pet_results)')
            }
            if 'item_type' not in columns:
                connection.execute(
                    'ALTER TABLE pet_results ADD COLUMN item_type TEXT')
            if 'pet_level' not in columns:
                connection.execute(
                    'ALTER TABLE pet_results ADD COLUMN pet_level INTEGER')
            if 'uid' not in columns:
                connection.execute(
                    'ALTER TABLE pet_results ADD COLUMN uid INTEGER')
            if 'fwq' not in columns:
                connection.execute(
                    'ALTER TABLE pet_results ADD COLUMN fwq INTEGER')
            connection.execute('''
                INSERT INTO pet_results (
                    recorded_at, operation, uid, fwq, item_type, pet_level,
                    pet_name, nickname, growth, physique, hp, strength,
                    attack, endurance, defense, agility, speed, intelligence,
                    magic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now().astimezone().isoformat(timespec='seconds'),
                operation,
                uid,
                fwq,
                item_type,
                pet['level'],
                _get_pet_name(pet['type_id']),
                pet['nick'],
                pet['grow_value'],
                pet['physique'],
                pet['hp_max'],
                pet['strength'],
                pet['attack'],
                pet['endurance'],
                pet['defense'],
                pet['quick'],
                pet['speed'],
                pet['intelligence'],
                pet['mp_max'],
            ))


class SocketSession:
    """为一条 TCP 连接维护持久化的拆包缓冲区和待处理响应。"""

    _MIN_PACKET_LENGTH = 18
    _MAX_PACKET_LENGTH = 10 * 1024 * 1024

    def __init__(self, sock):
        self.sock = sock
        self._recv_buffer = bytearray()
        self._pending_by_command = {}
        self._recv_lock = threading.Lock()

    def recv_packet(self, expected_command=None, timeout=None):
        """读取一个完整网络帧；不匹配 expected_command 的帧会暂存。

        timeout 是整个等待过程的总时限，不会因为收到心跳或
        其他协议而重新计时。
        """
        with self._recv_lock:
            pending = self._pending_by_command.get(expected_command)
            if expected_command is not None and pending:
                packet = pending.popleft()
                if not pending:
                    del self._pending_by_command[expected_command]
                return packet

            original_timeout = self.sock.gettimeout()
            deadline = (time.monotonic() + timeout
                        if timeout is not None else None)
            try:
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise socket.timeout('timed out')
                    packet = self._pop_complete_packet()
                    if packet is None:
                        if deadline is not None:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise socket.timeout('timed out')
                            self.sock.settimeout(remaining)
                        received_data = self.sock.recv(10000)
                        if not received_data:
                            raise ConnectionError('socket连接已关闭')
                        self._recv_buffer.extend(received_data)
                        continue

                    command_id = int.from_bytes(packet[4:6], byteorder='big')
                    if expected_command is None or command_id == expected_command:
                        return packet
                    self._pending_by_command.setdefault(command_id, deque()).append(packet)
            finally:
                if timeout is not None:
                    self.sock.settimeout(original_timeout)

    def _pop_complete_packet(self):
        if len(self._recv_buffer) < 4:
            return None

        packet_length = int.from_bytes(self._recv_buffer[:4], byteorder='big')
        if not self._MIN_PACKET_LENGTH <= packet_length <= self._MAX_PACKET_LENGTH:
            raise ValueError('响应包长度无效（%d）' % packet_length)
        if len(self._recv_buffer) < packet_length:
            return None

        packet = bytes(self._recv_buffer[:packet_length])
        del self._recv_buffer[:packet_length]
        if len(packet) < 6:
            raise ValueError('响应包头不完整')
        return packet


_SOCKET_SESSIONS = weakref.WeakKeyDictionary()
_SOCKET_SESSIONS_LOCK = threading.Lock()


def _get_socket_session(sock):
    """返回与 socket 绑定的会话，确保接收缓冲区不会随函数调用丢失。"""
    with _SOCKET_SESSIONS_LOCK:
        session = _SOCKET_SESSIONS.get(sock)
        if session is None:
            session = SocketSession(sock)
            _SOCKET_SESSIONS[sock] = session
        return session


def login_taomi(
        uid, pwd, model=1, fwq=0, return_server=False,
        return_user_info=False):
    uid_hex = hex(uid)
    str1 = ''.join(uid_hex)
    str2 = list()
    if uid_hex.__len__() % 2 == 0:
        for x in range(2, uid_hex.__len__(), 2):
            str2.append(int(('0x' + str1[x:x + 2]), base=16))

    else:
        str2.append(int(('0x0' + str1[2]), base=16))
        for x in range(3, uid_hex.__len__(), 2):
            str2.append(int(('0x' + str1[x:x + 2]), base=16))

    if str2.__len__() <= 3:
        str2.insert(0, 0)
    if str2.__len__() <= 3:
        str2.insert(0, 0)
    pwd1 = hashlib.md5(pwd.encode()).hexdigest()
    pwd_hex = pwd1.encode().hex()
    pwd1 = list()
    for x in range(0, 64, 2):
        pwd1.append(int(('0x' + pwd_hex[x:x + 2]), base=16))

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('49.234.206.24', 8989))
    req = struct.pack('148B', 0, 0, 0, 148, 0, 103, *str2, *(0, 0, 0, 1, 0, 0, 0, 0), *pwd1,
                      *(0, 0, 0, 0, 0, 0, 0, 7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 110, 111, 110, 101, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0))
    s.send(req)
    rec = s.recv(2048)
    if rec.__len__() != 42:
        print('登陆失败')
        return
    t = rec[22:38]
    # # CREATE_ROLE 107 可忽略
    # packet = [0, 0, 0, 162, 0, 107, *str2, 0, 0, 0, 2, 0, 0, 0, 0]
    # packet += t
    # packet = packet + [110, 111, 110, 101, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    #                    0, 0]
    # t1 = tuple(packet)
    # req = struct.pack('162B', *t1)
    # s.send(req)
    # rec = s.recv(2048)
    packet2 = [0, 0, 0, 38, 0, 105, *str2, 0, 0, 0, 1, 0, 0, 0, 0]
    packet2 += t
    packet2 = packet2 + [0, 0, 0, 0]
    t1 = tuple(packet2)
    req = struct.pack('38B', *t1)
    r = s.send(req)
    rec = s.recv(2048)

    fwq = fwq if model == 1 and fwq != 0 else random.randint(11, 20)
    if fwq in range(11, 21):
        s2.connect(('49.234.206.24', 18080))
        packet = [0, 0, 0, 174, 3, 233, *str2, 0, 0, 0, 184, 0, 0, 0, 0, 0, 0, 0, fwq]
        packet += t
        packet = packet + [0, 0, 0, 7, 0, 0, 0, 7, 110, 111, 110, 101, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        t1 = tuple(packet)
        req = struct.pack('174B', *t1)
        s2.send(req)
    elif fwq in range(1, 11):
        s2.connect(('49.234.206.24', 28080))
        packet = [0, 0, 0, 174, 3, 233, *str2, 0, 0, 0, 184, 0, 0, 0, 0, 0, 0, 0, fwq]
        packet += t
        packet = packet + [0, 0, 0, 7, 0, 0, 0, 7, 110, 111, 110, 101, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        t1 = tuple(packet)
        req = struct.pack('174B', *t1)
        s2.send(req)
    else:
        print('服务器选择错误')
        return

    user_info = _get_more_userinfo(s2, str2)
    profession = _PROFESSION_NAMES.get(user_info['profession'], str(user_info['profession']))
    # packet = [0, 0, 0, 38, 3, 236, *str2, 0, 0, 6, 31, 0, 0, 0, 0, 0, 0, 43, 194, 0, 0, 0, 0, 0, 0, 2, 63, 0, 0, 0, 159, 0, 0, 0, 0]
    # t1 = tuple(packet)
    # req = (struct.pack)('38B', *t1)
    # s2.send(req)
    print('%d登录成功,当前服务器%d' % (uid, fwq))
    print('昵称：%s，职业：%s，等级：%s' % (
        user_info['nick'], profession, user_info['level']))

    if return_server and return_user_info:
        return s, s2, str2, fwq, user_info
    if return_server:
        return s, s2, str2, fwq
    return s, s2, str2


def _parse_pet_info(data, offset, description):
    """按客户端 PetInfo 构造函数解析一条完整宠物记录。"""
    def read(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(data):
            raise ValueError('%s：宠物记录被截断' % description)
        value = struct.unpack_from(fmt, data, offset)[0]
        offset += size
        return value

    def read_bytes(size):
        nonlocal offset
        if offset + size > len(data):
            raise ValueError('%s：宠物记录被截断' % description)
        value = data[offset:offset + size]
        offset += size
        return value

    pet_id = read('>I')
    pet = {
        'pet_id': pet_id,
        'pet_id_bytes': tuple(pet_id.to_bytes(4, byteorder='big')),
        'type_id': read('>I'),
        'race': read('>B'),
        'flag': read('>I'),
        'nick': read_bytes(16).split(b'\x00', 1)[0].decode(
            'utf-8', errors='replace'),
        'level': read('>I'),
        'experience': read('>I'),
        'physique': read('>H'),
        'strength': read('>H'),
        'endurance': read('>H'),
        'quick': read('>H'),
        'intelligence': read('>H'),
        'attr_point_remaid': read('>H'),
        'attr_point_applied': read('>H'),
        'hp': read('>I'),
        'mp': read('>I'),
        'earth': read('>B'),
        'water': read('>B'),
        'fire': read('>B'),
        'wind': read('>B'),
        'injury_level': read('>I'),
        'status': _PET_POSITION_NAMES.get(read('>B'), '未知'),
        'hp_max': read('>I'),
        'mp_max': read('>I'),
        'attack': read('>H'),
        'defense': read('>H'),
        'speed': read('>H'),
        'spirit': read('>H'),
        'resume': read('>H'),
        'hit_rate': read('>H'),
        'avoid_rate': read('>H'),
        'critical': read('>H'),
        'fight_back': read('>H'),
        'grow_value': read('>H'),
    }

    skill_count = read('>I')
    if skill_count > (len(data) - offset - 6) // 9:
        raise ValueError('%s：技能数量与包长不匹配' % description)
    skills = []
    for _ in range(skill_count):
        skills.append({
            'skill_id': read('>I'),
            'level': max(1, read('>B')),
            'experience': read('>I'),
        })
    pet['skill_count'] = skill_count
    pet['skills'] = skills
    pet['is_reincarnation_enable'] = bool(read('>?'))
    pet['reincarnation_degree'] = read('>B')
    pet['additional_growth'] = read('>I')
    return pet, offset


def _get_pet_bag(s, str2):
    """请求并解析 1554（PET_GETLIST）宠物背包信息。"""
    packet = [0, 0, 0, 18, 6, 18, *str2, 0, 0, 4,
              random.randint(0, 255), 0, 0, 0, 0]
    s.send(struct.pack('18B', *packet))
    response = _receive_bag_response(s, str2, 1554, '宠物列表')
    if len(response) < _TMF_HEADER_SIZE + 4:
        raise ValueError('%s获取宠物列表失败：响应包被截断' % str2)

    pet_count = struct.unpack_from('>I', response, _TMF_HEADER_SIZE)[0]
    offset = _TMF_HEADER_SIZE + 4
    pets = []
    description = '%s获取宠物列表失败' % str2
    for _ in range(pet_count):
        pet, offset = _parse_pet_info(response, offset, description)
        pets.append(pet)

    if offset != len(response):
        raise ValueError('%s：包尾存在未解析数据' % description)
    return {'pet_count': pet_count, 'pets': pets}


def _get_pet_info(s, str2, pet_id):
    """通过宠物 ID 请求并解析 1553（PET_GETINFO）详细信息。"""
    if not isinstance(pet_id, int) or isinstance(pet_id, bool):
        raise ValueError('宠物ID必须是uint32正整数')
    if pet_id <= 0 or pet_id > 0xFFFFFFFF:
        raise ValueError('宠物ID必须是uint32正整数')

    packet = [0, 0, 0, 22, 6, 17, *str2, 0, 0, 4,
              random.randint(0, 255), 0, 0, 0, 0,
              *pet_id.to_bytes(4, byteorder='big')]
    s.send(struct.pack('22B', *packet))
    response = _receive_bag_response(s, str2, 1553, '宠物详细信息')

    description = '%s获取宠物详细信息失败' % str2
    pet, offset = _parse_pet_info(response, _TMF_HEADER_SIZE, description)
    if offset != len(response):
        raise ValueError('%s：包尾存在未解析数据' % description)
    if pet['pet_id'] != pet_id:
        raise ValueError('%s：宠物ID不匹配（期望%d，实际%d）' %
                         (description, pet_id, pet['pet_id']))
    return pet


def _parse_pet_store_info(data, offset, description):
    """按客户端 PetStorehouseInfo 构造函数解析一条仓库宠物记录。"""
    record_size = 33
    if offset + record_size > len(data):
        raise ValueError('%s：宠物记录被截断' % description)

    pet_id, type_id = struct.unpack_from('>II', data, offset)
    race = data[offset + 8]
    nick = data[offset + 9:offset + 25].split(b'\x00', 1)[0].decode(
        'utf-8', errors='replace')
    level, location = struct.unpack_from('>II', data, offset + 25)
    return {
        'pet_id': pet_id,
        'pet_id_bytes': tuple(pet_id.to_bytes(4, byteorder='big')),
        'type_id': type_id,
        'race': race,
        'nick': nick,
        'level': level,
        'location': location,
    }, offset + record_size


def _get_pet_store(s, str2):
    """请求并解析 1559（STORAGE_PET_LIST）宠物仓库信息。"""
    page_size = 910
    start_index = 0
    total_count = None
    pets = []
    description = '%s获取宠物仓库列表失败' % str2

    while True:
        packet = [0, 0, 0, 26, 6, 23, *str2, 0, 0, 6, 165,
                  0, 0, 0, 0,
                  *start_index.to_bytes(4, byteorder='big'),
                  *page_size.to_bytes(4, byteorder='big')]
        s.send(struct.pack('26B', *packet))
        response = _receive_bag_response(s, str2, 1559, '宠物仓库列表')

        if len(response) < _TMF_HEADER_SIZE + 12:
            raise ValueError('%s：缺少分页信息' % description)
        response_total, response_start, page_count = struct.unpack_from(
            '>III', response, _TMF_HEADER_SIZE)
        offset = _TMF_HEADER_SIZE + 12

        if response_start != start_index:
            raise ValueError('%s：分页起始位置不匹配' % description)
        if total_count is None:
            total_count = response_total
        elif response_total != total_count:
            raise ValueError('%s：分页总数不一致' % description)
        if response_start > total_count or page_count > total_count - response_start:
            raise ValueError('%s：分页数量超出宠物总数' % description)
        if page_count > (len(response) - offset) // 33:
            raise ValueError('%s：宠物数量与包长不匹配' % description)

        for _ in range(page_count):
            pet, offset = _parse_pet_store_info(response, offset, description)
            pets.append(pet)

        if offset != len(response):
            raise ValueError('%s：包尾存在未解析数据' % description)
        if len(pets) >= total_count:
            break
        if page_count == 0:
            raise ValueError('%s：分页未返回宠物数据' % description)
        start_index = response_start + page_count

    if len(pets) != total_count:
        raise ValueError('%s：实际宠物数量与总数不一致' % description)
    return {'pet_count': total_count, 'pets': pets}


def fpexp(exp, pet, uid, s):
    s1 = list()
    s1.append(int(exp / 65536))
    s1.append(int(exp / 256 % 256))
    s1.append(exp % 256)
    pet_id = pet['pet_id_bytes'] if isinstance(pet, dict) else pet[0:4]
    packet = [0, 0, 0, 26, 4, 12, *uid, 0, 0, 5, 63, 0, 0, 0, 0, *pet_id, 0, *s1]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)


def _get_exp_tree_experience(s, str2):
    """通过 2000 协议读取经验树剩余经验。"""
    response = _send_tmf_request(
        s, str2, _EXP_TREE_COMMAND, bytes(str2))
    payload = _parse_tmf_response_payload(response, _EXP_TREE_COMMAND)
    if len(payload) < 8:
        raise ValueError('2000协议回包缺少经验树经验字段')
    return struct.unpack_from('>I', payload, len(payload) - 8)[0]


def _get_bag_pet_by_id(s, str2, pet_id):
    if not isinstance(pet_id, int) or isinstance(pet_id, bool):
        raise ValueError('宠物ID必须是uint32正整数')
    if pet_id <= 0 or pet_id > 0xFFFFFFFF:
        raise ValueError('宠物ID必须是uint32正整数')
    pets = _get_pet_bag(s, str2)['pets']
    for pet in pets:
        if pet['pet_id'] == pet_id:
            return pet
    raise ValueError('所选宠物不在宠物背包中')


def _send_pet_experience(s, str2, pet_id_bytes, experience):
    remaining = experience
    packet_count = 0
    while remaining > 0:
        packet_exp = min(remaining, _MAX_EXP_PER_PACKET)
        fpexp(packet_exp, pet_id_bytes, str2, s)
        remaining -= packet_exp
        packet_count += 1
        if packet_count % 10 == 0 and remaining > 0:
            time.sleep(0.25)


def _auto_allocate_pet_attr_points(s, str2, pet_id_bytes):
    packet = _build_tmf_request(
        str2, _PET_AUTO_ALLOCATE_ATTR_POINTS_COMMAND, pet_id_bytes)
    s.send(packet)


def _require_exp_tree_experience(s, str2, required):
    available = _get_exp_tree_experience(s, str2)
    if required > available:
        raise ValueError(
            '经验树经验不足：需要%d，当前剩余%d' % (required, available))
    return available


def _validate_pet_experience(experience):
    if not isinstance(experience, int) or isinstance(experience, bool):
        raise ValueError('分配经验必须是正整数')
    if experience <= 0 or experience > 0xFFFFFFFF:
        raise ValueError('分配经验必须在1到4294967295之间')


def _pet_rebirth_stage_experience(pet):
    rebirth = pet['reincarnation_degree']
    if rebirth > 10:
        raise ValueError('宠物转生次数超过10，无法继续分配经验')
    stages = [(
        rebirth,
        max(0, jsexp(rebirth, 0) - pet['experience']),
    )]
    stages.extend(
        (stage, jsexp(stage, 0))
        for stage in range(rebirth + 1, 11)
    )
    return stages


def _allocate_pet_experience(s, str2, pet_id, experience):
    _validate_pet_experience(experience)
    pet = _get_bag_pet_by_id(s, str2, pet_id)
    _require_exp_tree_experience(s, str2, experience)
    _send_pet_experience(s, str2, pet['pet_id_bytes'], experience)
    _auto_allocate_pet_attr_points(s, str2, pet['pet_id_bytes'])


def _allocate_all_pet_experience(s, str2, pet_id):
    """向指定宠物分配经验树全部经验，达标时自动转生。"""
    pet = _get_bag_pet_by_id(s, str2, pet_id)
    stage_experience = _pet_rebirth_stage_experience(pet)
    required_to_max = sum(required for _stage, required in stage_experience)
    if required_to_max <= 0:
        raise ValueError('所选宠物已达到10转99级')
    available = _get_exp_tree_experience(s, str2)
    if available <= 0:
        raise ValueError('经验树没有可分配经验')
    experience = min(available, required_to_max)

    remaining = experience
    for stage, required in stage_experience:
        allocated = min(remaining, required)
        if allocated > 0:
            _send_pet_experience(
                s, str2, pet['pet_id_bytes'], allocated)
            remaining -= allocated
        if allocated < required:
            break
        if stage < 10:
            zs(pet['pet_id_bytes'], str2, s)
        if remaining == 0:
            break

    _auto_allocate_pet_attr_points(s, str2, pet['pet_id_bytes'])
    return experience


def _upgrade_pet_to_zero_99(s, str2, pet_id):
    pet = _get_bag_pet_by_id(s, str2, pet_id)
    if pet['reincarnation_degree'] != 0:
        raise ValueError('一键升级0转99级仅适用于0转宠物')
    required = max(0, _ZERO_99_TARGET_EXP - pet['experience'])
    if required == 0 or pet['level'] >= 99:
        raise ValueError('所选宠物已达到0转99级')
    _require_exp_tree_experience(s, str2, required)
    _send_pet_experience(s, str2, pet['pet_id_bytes'], required)
    _auto_allocate_pet_attr_points(s, str2, pet['pet_id_bytes'])


def _upgrade_pet_to_ten_99(s, str2, pet_id):
    pet = _get_bag_pet_by_id(s, str2, pet_id)
    rebirth = pet['reincarnation_degree']
    stage_experience = _pet_rebirth_stage_experience(pet)
    required = sum(experience for _stage, experience in stage_experience)
    if required == 0 or (rebirth == 10 and pet['level'] >= 99):
        raise ValueError('所选宠物已达到10转99级')
    _require_exp_tree_experience(s, str2, required)

    for stage, experience in stage_experience:
        _send_pet_experience(s, str2, pet['pet_id_bytes'], experience)
        if stage < 10:
            zs(pet['pet_id_bytes'], str2, s)
    _auto_allocate_pet_attr_points(s, str2, pet['pet_id_bytes'])


def _upgrade_pet_to_ten_99_with_books(s, str2, pet_id):
    """补足经验树缺口后，将指定宠物升至10转99级。"""
    pet = _get_bag_pet_by_id(s, str2, pet_id)
    stage_experience = _pet_rebirth_stage_experience(pet)
    required = sum(experience for _stage, experience in stage_experience)
    if required <= 0:
        raise ValueError('所选宠物已达到10转99级')

    available_experience = _get_exp_tree_experience(s, str2)
    deficit = max(0, required - available_experience)
    book_count = (
        deficit + _EXPERIENCE_PER_BOOK - 1
    ) // _EXPERIENCE_PER_BOOK
    if book_count > 0:
        openbook(s, str2, book_count)

    _upgrade_pet_to_ten_99(s, str2, pet_id)
    return book_count


def zs(pet, uid, s):
    pet_id = pet['pet_id_bytes'] if isinstance(pet, dict) else pet[0:4]
    packet = [0, 0, 0, 22, 15, 163, *uid, 0, 0, 5, 187, 0, 0, 0, 0, *pet_id]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    print('转生成功')


def jsexp(tr, low):
    HighLevelList = [
        '1080367', '1088762', '1097386', '1106240', '1115329', '1124654',
        '1134220', '1144028', '1154083', '1164386',
        '1174942',
        '1185752', '1196821', '1208150', '1219744', '1231604', '1243735',
        '1256138', '1268818', '1281776',
        '1295017', '1308542',
        '1322356', '1336460', '1350859', '1365554', '1380550', '1395848',
        '1411453', '1427366',
        '1443592', '1460132', '1476991',
        '1494170', '1511674']
    Section = ['17850625', '21420750', '25704900', '30845879', '37015057', '44418067',
               '53301681', '63962019', '76754417', '92105299',
               '110526369']
    tr = int(tr)
    low = int(low)
    if tr != 10:
        high = int(50 + 5 * tr)
    else:
        high = 99
    exphigh = 0
    explow = 0
    if low >= high:
        print('等级下限不能超过等级上限！')
    else:
        if low < 65 and high < 65:
            explow = int(low ** 4 * 1.2 ** tr + 0.5)
            exphigh = int(high ** 4 * 1.2 ** tr + 0.5)
            exp = exphigh - explow
        else:
            if low < 65 and high > 64:
                explow = int(low ** 4 * 1.2 ** tr + 0.5)
                while high - 65:
                    exphigh += int(int(HighLevelList[high - 66]) * 1.2 ** tr + 0.5)
                    high -= 1

                exp = exphigh - explow + int(Section[tr])
            else:
                while high - 65:
                    exphigh += int(int(HighLevelList[high - 66]) * 1.2 ** tr + 0.5)
                    high -= 1

                while low - 65:
                    explow += int(int(HighLevelList[low - 66]) * 1.2 ** tr + 0.5)
                    low -= 1

                exp = exphigh - explow
        return exp + 10


def _receive_bag_response(s, str2, command_id, description):
    """从 socket 中取出指定命令的一个完整网络帧。"""
    try:
        return _get_socket_session(s).recv_packet(expected_command=command_id)
    except ConnectionError as exc:
        raise ConnectionError('%s获取%s失败：连接已关闭' % (str2, description)) from exc
    except ValueError as exc:
        raise ValueError('%s获取%s失败：%s' % (str2, description, exc)) from exc


def _parse_equipment_record(data, offset, str2):
    """按 SingleItemInfo.setEquipInfo 解析一条装备记录。"""
    def read(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(data):
            raise ValueError('%s解析装备信息失败：装备记录被截断' % str2)
        value = struct.unpack_from(fmt, data, offset)[0]
        offset += size
        return value

    unique_id = read('>I')
    good_id = read('>I')
    grid_id = read('>I')
    level = read('>H')
    color_id = read('>I')
    validday = read('>I')
    max_durability = read('>H')
    durability = read('>H')
    max_hp = read('>i')
    max_mp = read('>i')

    attack = read('>h')
    magic_attack = read('>h')
    defense = read('>h')
    magic_defense = read('>h')
    speed = read('>h')
    spirit = read('>h')
    recovery = read('>h')
    hit = read('>h')
    dodge = read('>h')
    critical = read('>h')
    counter = read('>h')
    resistances = [read('>h') for _ in range(6)]

    crystal_attr = read('>I')
    bless_type = read('>I')
    activated = read('>I')
    add_bless_value = read('>I')

    gem_count = read('>I')
    if gem_count > (len(data) - offset) // 4:
        raise ValueError('%s解析装备信息失败：宝石数量与包长不匹配' % str2)
    gem_ids = [read('>I') for _ in range(gem_count)]

    equip_kind_identified = read('>I')
    stone_count = read('>I')
    if stone_count > (len(data) - offset) // 4:
        raise ValueError('%s解析装备信息失败：镶嵌石数量与包长不匹配' % str2)
    stone_ids = [read('>I') for _ in range(stone_count)]

    return {
        'instance_id': tuple(unique_id.to_bytes(4, byteorder='big')),
        'item_id': good_id,
        'grid_id': grid_id,
        'level': level,
        'equip_color_id': color_id,
        'validday': validday,
        'durability': durability,
        'max_durability': max_durability,
        'max_hp': max_hp,
        'max_mp': max_mp,
        'attack': attack,
        'magic_attack': magic_attack,
        'defense': defense,
        'magic_defense': magic_defense,
        'speed': speed,
        'spirit': spirit,
        'recovery': recovery,
        'hit': hit,
        'dodge': dodge,
        'critical': critical,
        'counter': counter,
        'resist_poison': resistances[0],
        'resist_stone': resistances[1],
        'resist_sleep': resistances[2],
        'resist_inebriation': resistances[3],
        'resist_confusion': resistances[4],
        'resist_oblivion': resistances[5],
        'crystal_attr': crystal_attr,
        'bless_type': bless_type,
        'activated': activated,
        'add_bless_value': add_bless_value,
        'gem_identification_status': equip_kind_identified,
        'equip_kind_identified': equip_kind_identified,
        'socket_count': gem_count,
        'gem_count': gem_count,
        'gem_ids': gem_ids,
        'equip_level_identify': stone_count,
        'stone_count': stone_count,
        'stone_ids': stone_ids,
    }, offset


def _get_more_userinfo(s, str2):
    """请求并解析 1006（GET_MORE_USERINFO）的人物详细信息。"""
    packet = [0, 0, 0, 22, 3, 238, *str2, 0, 0, 5, 175, 0, 0, 0, 0, *str2]
    s.send(struct.pack('22B', *packet))

    response = _receive_bag_response(s, str2, 1006, '人物信息')
    offset = 18

    def read(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(response):
            raise ValueError('%s获取人物信息失败：响应包被截断' % str2)
        value = struct.unpack_from(fmt, response, offset)[0]
        offset += size
        return value

    def read_bytes(size):
        nonlocal offset
        if offset + size > len(response):
            raise ValueError('%s获取人物信息失败：响应包被截断' % str2)
        value = response[offset:offset + size]
        offset += size
        return value

    user_id = read('>I')
    nick = read_bytes(16).split(b'\x00', 1)[0].decode('utf-8', errors='replace')
    fields = {
        'user_id': user_id,
        'nick': nick,
        'flag': read('>I'),
        'vip_level': read('>I'),
        'vip_energy': read('>I'),
        'vip_begin': read('>I'),
        'vip_end': read('>I'),
        'hero_cup_team_id': read('>I'),
        'color': read('>I'),
        'reg_time': read('>I'),
        'race': read('>B'),
        'profession': read('>B'),
        'profession_phase': read('>I'),
        'honor': read('>I'),
        'xiaomee': read('>I'),
        'pk_point': read('>I'),
        'energy': read('>I'),
        'level': read('>I'),
        'experience': read('>I'),
        'physique': read('>H'),
        'strength': read('>H'),
        'endurance': read('>H'),
        'quick': read('>H'),
        'intelligence': read('>H'),
        'attr_add': read('>H'),
        'hp': read('>I'),
        'mp': read('>I'),
        'earth': read('>B'),
        'water': read('>B'),
        'fire': read('>B'),
        'wind': read('>B'),
        'injured_level': read('>I'),
        'change_body_id': read('>I'),
        'map_id': read('>I'),
        'map_type': read('>I'),
        'pos_x': read('>I'),
        'pos_y': read('>I'),
        'base_action': read('>I'),
        'adv_action': read('>I'),
        'direction': read('>B'),
        'battle_in_front': read('>B'),
        'team_id': read('>I'),
        'team_member_index': read('>I'),
        'hp_max': read('>I'),
        'mp_max': read('>I'),
        'attack': read('>H'),
        'defense': read('>H'),
        'magic_defense': read('>H'),
        'speed': read('>H'),
        'spirit': read('>H'),
        'restore': read('>H'),
        'hit_rate': read('>H'),
        'avoid_rate': read('>H'),
        'critical': read('>H'),
        'attack_back': read('>H'),
        'anti_poison': read('>H'),
        'anti_stone': read('>H'),
        'anti_sleep': read('>H'),
        'anti_curse': read('>H'),
        'anti_confusion': read('>H'),
        'anti_forget': read('>H'),
    }

    # 客户端读取并丢弃 32 字节保留区，不能把它误当成装备数量。
    fields['reserved'] = read_bytes(32)
    suit_item_count = read('>B')
    if suit_item_count > (len(response) - offset) // 96:
        raise ValueError('%s获取人物信息失败：装备数量与包长不匹配' % str2)

    suit_items = []
    for _ in range(suit_item_count):
        item, offset = _parse_equipment_record(response, offset, str2)
        suit_items.append(item)

    if offset != len(response):
        raise ValueError('%s获取人物信息失败：包尾存在未解析数据' % str2)
    fields['suit_item_count'] = suit_item_count
    fields['suit_items'] = suit_items
    fields['detail_equip_item_list'] = suit_items
    return fields


def _get_prop_bag_info(s, str2):
    """
    获取并解析道具背包中的物品信息。
    :param s: socket连接
    :param str2: 米米号
    :return: 结构化物品信息列表和物品数量
    """
    packet = [0, 0, 0, 18, 4, 85, *str2, 0, 0, 4, 233, 0, 0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('18B',), *t1)
    s.send(req)

    response = _receive_bag_response(s, str2, 1109, '道具列表')
    offset = 18
    if len(response) - offset < 4:
        raise ValueError('%s获取道具列表失败：缺少物品数量' % str2)
    prop_count = struct.unpack_from('>I', response, offset)[0]
    offset += 4
    if prop_count > (len(response) - offset) // 12:
        raise ValueError('%s获取道具列表失败：物品数量与包长不匹配' % str2)

    prop_info = []
    for _ in range(prop_count):
        good_id, grid_id, quantity = struct.unpack_from('>IIi', response, offset)
        offset += 12
        prop_info.append({
            'item_id': good_id,
            'grid_id': grid_id,
            # setGoodInfo 使用 readInt()，客户端对负数数量按 0 处理。
            'quantity': max(0, quantity),
        })

    if offset != len(response):
        raise ValueError('%s获取道具列表失败：包尾存在未解析数据' % str2)
    return prop_info, prop_count


def _get_prop_store_info(s, str2):
    """
    获取并解析道具仓库中的物品信息。
    :param s: socket连接
    :param str2: 米米号
    :return: 结构化物品信息列表和物品数量
    """
    packet = [0, 0, 0, 18, 4, 97, *str2, 0, 0, 4, 233, 0, 0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('18B',), *t1)
    s.send(req)

    response = _receive_bag_response(s, str2, 1121, '道具仓库列表')
    offset = 18
    if len(response) - offset < 4:
        raise ValueError('%s获取道具仓库列表失败：缺少物品数量' % str2)
    prop_count = struct.unpack_from('>I', response, offset)[0]
    offset += 4
    if prop_count > (len(response) - offset) // 8:
        raise ValueError('%s获取道具仓库列表失败：物品数量与包长不匹配' % str2)

    prop_info = []
    for _ in range(prop_count):
        good_id, quantity = struct.unpack_from('>II', response, offset)
        offset += 8
        prop_info.append({
            'item_id': good_id,
            'quantity': quantity,
        })

    if offset != len(response):
        raise ValueError('%s获取道具仓库列表失败：包尾存在未解析数据' % str2)
    return prop_info, prop_count


def _exchange_item(s, str2, npc_id, item_index, quantity):
    '''
    兑换物品(水晶、巨石碎片等)
    :param s: socket连接
    :param str2: 玩家米米号
    :param npc_id: NPC ID
    :param item_index: 物品索引
    :param quantity: 数量
    '''
    quantity_byte = [quantity // 256, quantity % 256]
    packet = [0, 0, 0, 0x1a, 0x04, 0x68, *str2, 0, 0, random.randint(5, 6), random.randint(0, 255), 0, 0, 0, 0, 0, 0,
              npc_id, item_index, 0, 0, *quantity_byte]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)


def openbook(s, str2, count=None):
    packet = [0, 0, 0, 26, 4, 105, *str2, 0, 0, 5, 149, 0, 0, 0, 0, 0, 5, 87, 98, 0, 0, 0, 1]
    t1 = tuple(packet)
    req = struct.pack('26B', *t1)
    if count is None:
        count = int(input('请输入开启数量'))
    if count <= 0:
        raise ValueError('使用数量必须大于0')
    bag_items, _ = _get_prop_bag_info(s, str2)
    store_items, _ = _get_prop_store_info(s, str2)
    bag_quantity = sum(
        item['quantity'] for item in bag_items
        if item['item_id'] == _EXPERIENCE_BOOK_ID
    )
    store_quantity = sum(
        item['quantity'] for item in store_items
        if item['item_id'] == _EXPERIENCE_BOOK_ID
    )
    available = bag_quantity + store_quantity
    if count > available:
        raise ValueError(
            '经验书数量不足：需要%d，背包%d，仓库%d，合计%d' %
            (count, bag_quantity, store_quantity, available))
    store_fetch_quantity = max(0, count - bag_quantity)
    if store_fetch_quantity > 0:
        _fetch_item_from_store(
            s, str2, _EXPERIENCE_BOOK_ID, store_fetch_quantity)
    for i in range(count):
        s.send(req)
        if i % 100 == 0:
            time.sleep(0.5)


def _build_tmf_request(str2, command_id, payload):
    """按客户端 TMF 格式构造请求包。"""
    if len(str2) != 4:
        raise ValueError('str2 必须是 4 字节用户标识')
    if not 0 <= command_id <= 0xffff:
        raise ValueError('命令号超出 uint16 范围')

    payload = bytes(payload)
    packet_length = _TMF_HEADER_SIZE + len(payload)
    return struct.pack(
        '>IH4B4B4B',
        packet_length,
        command_id,
        *str2,
        0,
        0,
        random.randint(5, 6),
        random.randint(0, 255),
        0,
        0,
        0,
        0,
    ) + payload


def _parse_tmf_response_payload(packet, expected_command):
    """校验 TMF 回包头并返回负载；头部最后 4 字节为错误码。"""
    if len(packet) < _TMF_HEADER_SIZE:
        raise ValueError('%d协议回包头不完整' % expected_command)

    packet_length, command_id = struct.unpack_from('>IH', packet)
    if packet_length != len(packet):
        raise ValueError(
            '%d协议回包长度不一致（声明%d，实际%d）' %
            (expected_command, packet_length, len(packet)))
    if command_id != expected_command:
        raise ValueError(
            '协议命令不匹配（期望%d，实际%d）' %
            (expected_command, command_id))

    error_code = struct.unpack_from('>I', packet, 14)[0]
    if error_code != 0:
        raise RuntimeError(
            '%d协议返回错误码%d（0x%08X）' %
            (expected_command, error_code, error_code))
    return packet[_TMF_HEADER_SIZE:]


def _send_tmf_request(s, str2, command_id, payload, timeout=10):
    packet = _build_tmf_request(str2, command_id, payload)
    s.sendall(packet)
    return _get_socket_session(s).recv_packet(
        expected_command=command_id, timeout=timeout)


def _parse_pet_attr_points_response(packet, expected_command):
    """解析 1565/1606 回包中的完整 PetInfo。"""
    if expected_command not in (
            _PET_RESET_ATTR_POINTS_COMMAND,
            _PET_ALLOCATE_ATTR_POINTS_COMMAND):
        raise ValueError('不支持的宠物加点协议：%d' % expected_command)

    payload = _parse_tmf_response_payload(packet, expected_command)
    pet, offset = _parse_pet_info(
        payload, 0, '%d协议回包解析失败' % expected_command)
    if offset != len(payload):
        raise ValueError('%d协议回包存在未解析数据' % expected_command)
    return pet


def _reset_pet_attr_points(s, str2, pet_id):
    """重置指定宠物的自由加点，并返回服务端回传的完整 PetInfo。"""
    pet_id = int(pet_id)
    reset_payload = struct.pack(
        '>II', pet_id, _PET_RESET_ATTR_POINTS_ITEM_ID)
    reset_packet = _send_tmf_request(
        s, str2, _PET_RESET_ATTR_POINTS_COMMAND, reset_payload)
    reset_pet = _parse_pet_attr_points_response(
        reset_packet, _PET_RESET_ATTR_POINTS_COMMAND)
    if reset_pet['pet_id'] != pet_id:
        raise RuntimeError(
            '1565回包宠物ID不匹配（期望%d，实际%d）' %
            (pet_id, reset_pet['pet_id']))
    return reset_pet


def _allocate_pet_attr_points(s, str2, pet_id, points):
    """为指定宠物分配五项自由点，并返回服务端回传的完整 PetInfo。"""
    pet_id = int(pet_id)
    points = tuple(points)
    if len(points) != len(_ATTR_NAMES):
        raise ValueError('宠物自由加点必须包含五项')
    if any(not isinstance(value, int) or isinstance(value, bool)
           for value in points):
        raise ValueError('宠物自由加点必须全部是整数')
    if any(value < 0 or value > 0xffff for value in points):
        raise ValueError('宠物每项自由加点必须在0到65535之间')

    allocate_payload = struct.pack('>I5H', pet_id, *points)
    allocate_packet = _send_tmf_request(
        s, str2, _PET_ALLOCATE_ATTR_POINTS_COMMAND, allocate_payload)
    result_pet = _parse_pet_attr_points_response(
        allocate_packet, _PET_ALLOCATE_ATTR_POINTS_COMMAND)
    if result_pet['pet_id'] != pet_id:
        raise RuntimeError(
            '1606回包宠物ID不匹配（期望%d，实际%d）' %
            (pet_id, result_pet['pet_id']))
    return result_pet


def _learn_pet_skill(s, str2, pet_id, skill_id, skill_level, npc_id):
    """通过 1601 为宠物学习指定等级的技能。"""
    values = (
        (npc_id, 'NPC ID'),
        (pet_id, '宠物ID'),
        (skill_id, '技能ID'),
    )
    normalized = []
    for value, label in values:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError('%s必须是整数' % label)
        if value < 1 or value > 0xffffffff:
            raise ValueError('%s超出uint32范围' % label)
        normalized.append(value)
    if (not isinstance(skill_level, int) or isinstance(skill_level, bool)
            or skill_level not in range(1, 11)):
        raise ValueError('技能等级必须是1到10之间的整数')

    # 客户端 SocketConnection.send 的实参顺序为
    # npcID、petId、skillId、skillLv，四项均按 uint32 编码。
    payload = struct.pack(
        '>4I', normalized[0], normalized[1], normalized[2], skill_level)
    packet = _send_tmf_request(
        s, str2, _PET_LEARN_SKILL_COMMAND, payload)
    response = _parse_tmf_response_payload(
        packet, _PET_LEARN_SKILL_COMMAND)
    if len(response) != 17:
        raise ValueError('1601协议回包长度异常（期望17，实际%d）' % len(response))

    response_pet_id, response_skill_id, response_level, xiaomee, cost = (
        struct.unpack('>IIBII', response)
    )
    if response_pet_id != normalized[1]:
        raise RuntimeError(
            '1601回包宠物ID不匹配（期望%d，实际%d）' %
            (normalized[1], response_pet_id))
    if response_skill_id != normalized[2]:
        raise RuntimeError(
            '1601回包技能ID不匹配（期望%d，实际%d）' %
            (normalized[2], response_skill_id))
    if response_level != skill_level:
        raise RuntimeError(
            '1601回包技能等级不匹配（期望%d，实际%d）' %
            (skill_level, response_level))
    return {
        'pet_id': response_pet_id,
        'skill_id': response_skill_id,
        'skill_level': response_level,
        'xiaomee': xiaomee,
        'cost': cost,
    }


def _load_xd_pets(s, str2):
    """加载洗点所需的宠物数据，复用统一的宠物背包请求和解析逻辑。

    返回 ``_get_pet_bag`` 的完整结果（包含 ``pet_count`` 和结构化的
    ``pets`` 列表），避免洗点流程再次按字节偏移解析宠物记录。
    """
    return _get_pet_bag(s, str2)


def _get_xd_item_quantities(s, str2):
    """获取洗点所用五种道具在背包中的数量。"""
    prop_info, _ = _get_prop_bag_info(s, str2)
    quantities = {item_id: 0 for item_id in _XD_ITEM_IDS}
    for prop in prop_info:
        item_id = prop['item_id']
        if item_id in quantities:
            quantities[item_id] += prop['quantity']
    return quantities


def eatwz(s, str2, pet, num, item_quantities):
    item_id = _XD_ITEM_IDS[num]
    packet = [0, 0, 0, 26, 6, 34, *str2, 0, 0, 5, 172, 0, 0, 0, 0,
              *pet['pet_id_bytes'], *item_id.to_bytes(4, byteorder='big')]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)
    time.sleep(0.2)
    item_quantities[item_id] -= 1
    print('%s剩余数量：%d' % (_XD_ITEM_TYPES[num], item_quantities[item_id]))


def _fetch_item_from_store(s, str2, item_id, quantity):
    '''
    从仓库中获取指定id的物品
    :param s: socket连接
    :param str2: 玩家米米号
    :param item_id: 物品ID
    :param quantity: 数量
    '''
    item_id_bytes = int(item_id).to_bytes(4, byteorder='big')
    quantity_bytes = int(quantity).to_bytes(4, byteorder='big')
    packet = [0, 0, 0, 30, 4, 99, *str2, 0, 0, random.randint(5, 6), random.randint(0, 255), 0, 0, 0, 0,
              0, 0, 0, 0, *item_id_bytes, *quantity_bytes]
    t1 = tuple(packet)
    req = struct.pack(*('30B',), *t1)
    s.send(req)


def _open_pet_eggs(s, str2, item_id, quantity):
    egg_ids = {egg_id for egg_id, _name in _PET_EGG_ITEMS}
    if item_id not in egg_ids:
        raise ValueError('所选物品不是已知的精灵蛋')
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValueError('开蛋数量必须是正整数')
    if quantity <= 0 or quantity > 0xFFFFFFFF:
        raise ValueError('开蛋数量必须在1到4294967295之间')

    bag_items, _ = _get_prop_bag_info(s, str2)
    store_items, _ = _get_prop_store_info(s, str2)
    bag_quantity = sum(
        item['quantity'] for item in bag_items
        if item['item_id'] == item_id
    )
    store_quantity = sum(
        item['quantity'] for item in store_items
        if item['item_id'] == item_id
    )
    available = bag_quantity + store_quantity
    if quantity > available:
        raise ValueError(
            '%s数量不足：需要%d，背包%d，仓库%d，合计%d' %
            (_get_item_name(item_id), quantity, bag_quantity,
             store_quantity, available))

    store_fetch_quantity = max(0, quantity - bag_quantity)
    if store_fetch_quantity > 0:
        _fetch_item_from_store(
            s, str2, item_id, store_fetch_quantity)
    packet = _build_tmf_request(
        str2, _OPEN_PET_EGG_COMMAND, item_id.to_bytes(4, byteorder='big'))
    for index in range(quantity):
        s.send(packet)
        if (index + 1) % 6 == 0 and index + 1 < quantity:
            time.sleep(1)
    time.sleep(1)


def _change_pet_status(s, str2, pet, status):
    """通过 1551（PET_CHANGE_FLAG）切换宠物所在位置。"""
    if status not in (0, 1):
        raise ValueError('宠物目标位置必须是0（仓库）或1（背包）')

    if isinstance(pet, dict):
        pet_id = pet.get('pet_id_bytes', pet.get('pet_id'))
    else:
        pet_id = pet

    if isinstance(pet_id, int) and not isinstance(pet_id, bool):
        if pet_id <= 0 or pet_id > 0xFFFFFFFF:
            raise ValueError('宠物ID必须是有效的uint32正整数')
        pet_id_bytes = pet_id.to_bytes(4, byteorder='big')
    else:
        try:
            pet_id_bytes = bytes(pet_id[0:4])
        except (TypeError, ValueError) as exc:
            raise ValueError('宠物ID必须是uint32或4字节序列') from exc
        if len(pet_id_bytes) != 4 or int.from_bytes(
                pet_id_bytes, byteorder='big') == 0:
            raise ValueError('宠物ID必须是非零的4字节序列')

    packet = [0, 0, 0, 26, 6, 15, *str2, 0, 0, 5, 93,
              0, 0, 0, 0, *pet_id_bytes, 0, 0, 0, status]
    s.send(struct.pack('26B', *packet))


def _pet_change_name(s, str2, pet, name):
    """通过 1552（PET_CHANGE_NICK）修改宠物昵称。"""
    if isinstance(pet, dict):
        pet_id = pet.get('pet_id_bytes', pet.get('pet_id'))
    else:
        pet_id = pet

    if isinstance(pet_id, int) and not isinstance(pet_id, bool):
        if pet_id <= 0 or pet_id > 0xFFFFFFFF:
            raise ValueError('宠物ID必须是有效的uint32正整数')
    else:
        try:
            pet_id_bytes = bytes(pet_id[0:4])
        except (TypeError, ValueError) as exc:
            raise ValueError('宠物ID必须是uint32或4字节序列') from exc
        if len(pet_id_bytes) != 4:
            raise ValueError('宠物ID必须是uint32或4字节序列')
        pet_id = int.from_bytes(pet_id_bytes, byteorder='big')
        if pet_id == 0:
            raise ValueError('宠物ID必须是非零的4字节序列')

    if not isinstance(name, str):
        raise ValueError('宠物昵称必须是字符串')
    name_bytes = name.encode('utf-8')[:16].ljust(16, b'\x00')
    payload = struct.pack('>I', pet_id) + name_bytes
    s.send(_build_tmf_request(str2, 1552, payload))


def _pet_back_store(s, str2, pet):
    """将宠物放回仓库（1551 状态 0）。"""
    _change_pet_status(s, str2, pet, 0)


def _pet_back_bag(s, str2, pet):
    """将宠物从仓库取回背包（1551 状态 1）。"""
    _change_pet_status(s, str2, pet, 1)


def _pet_release(s, str2, pet):
    """通过 1561（PET_RELEASE）放生仓库中的宠物。"""
    if isinstance(pet, dict):
        pet_id = pet.get('pet_id_bytes', pet.get('pet_id'))
    else:
        pet_id = pet

    if isinstance(pet_id, int) and not isinstance(pet_id, bool):
        if pet_id <= 0 or pet_id > 0xFFFFFFFF:
            raise ValueError('宠物ID必须是有效的uint32正整数')
        pet_id_bytes = pet_id.to_bytes(4, byteorder='big')
    else:
        try:
            pet_id_bytes = bytes(pet_id[0:4])
        except (TypeError, ValueError) as exc:
            raise ValueError('宠物ID必须是uint32或4字节序列') from exc
        if len(pet_id_bytes) != 4 or int.from_bytes(
                pet_id_bytes, byteorder='big') == 0:
            raise ValueError('宠物ID必须是非零的4字节序列')

    s.send(_build_tmf_request(str2, _PET_RELEASE_COMMAND, pet_id_bytes))


def _build_map_enter_packet(str2, map_id, entry_x, entry_y, sequence=None):
    """构造进入指定地图和落点的 1004 请求。"""
    if len(str2) != 4:
        raise ValueError('str2 必须是 4 字节用户标识')
    sequence = random.randint(0, 255) if sequence is None else sequence
    return bytes([0, 0, 0, 0x26, 0x03, 0xec, *str2, 0, 0, 5,
                  sequence, 0, 0, 0, 0, 0, 0,
                  (map_id >> 8) & 0xff, map_id & 0xff,
                  0, 0, 0, 0, 0, 0,
                  (entry_x >> 8) & 0xff, entry_x & 0xff,
                  0, 0, (entry_y >> 8) & 0xff, entry_y & 0xff,
                  0, 0, 0, 0])


def _enter_map(s, str2, map_id, entry_x, entry_y, timeout=10):
    """进入指定地图，并等待服务端确认 1004 请求成功。"""
    packet = _build_map_enter_packet(str2, map_id, entry_x, entry_y)
    s.sendall(packet)
    response = _get_socket_session(s).recv_packet(
        expected_command=1004, timeout=timeout)
    _parse_tmf_response_payload(response, 1004)
    return response


def exchangelb(s, str2, type, count):
    if count <= 0:
        print('兑换数量必须大于0')
        return
    if type == 1: # 巨石碎片->奖牌(7:1),2,3巨石->大丸子
        max_batch_size = 50
        remaining_count = count
        while remaining_count > 0:
            batch_size = min(remaining_count, max_batch_size)
            _fetch_item_from_store(s, str2, 290011, batch_size * 7)
            _exchange_item(s, str2, 0x4e, 0x72, batch_size)  
            remaining_count -= batch_size
            time.sleep(0.1)               
    elif type == 2: # 巨石碎片->奖牌->宝物(4*7:1)
        max_batch_size = 50
        remaining_count = count
        while remaining_count > 0:
            batch_size = min(remaining_count, max_batch_size)
            _fetch_item_from_store(s, str2, 290011, batch_size * 28)
            _exchange_item(s, str2, 0x4e, 0x72, batch_size * 4) 
            _exchange_item(s, str2, 0x27, 0x79, batch_size)  
            remaining_count -= batch_size
            time.sleep(0.1)               
    elif type == 3: # 巨石碎片->大丸子(2:1)
        max_batch_size = 50
        remaining_count = count
        while remaining_count > 0:
            batch_size = min(remaining_count, max_batch_size)
            _fetch_item_from_store(s, str2, 290011, batch_size * 2)
            _exchange_item(s, str2, 0x4e, 0x74, batch_size)
            remaining_count -= batch_size
            time.sleep(0.1) 
    else:
        print('兑换类型错误')
        return

    print('兑换成功')
