from urllib import parse
import time, struct, random, socket, hashlib, threading, weakref
import sqlite3
import sys
from collections import deque
from contextlib import closing
from datetime import datetime
from pathlib import Path

from uncompyle6.parsers.reducecheck import tryexcept

from item_names import ITEM_NAMES as _ITEM_NAMES
from pet_names import PET_NAMES as _PET_NAMES

cz = 0
wx = [0, 0, 0, 0, 0]
xd_count = 0
xd_max_count = 0
xd_mode = None
mmh = 0
mmh_mm = 0

# 与客户端 ProfessionType 中的职业编号保持一致。
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
_ROLE_ALLOCATE_ATTR_POINTS_COMMAND = 1027
_RESET_ATTR_POINTS_COMMAND = 1115
_ROLE_RESET_ATTR_POINTS_ITEM_ID = 360007
_PET_ALLOCATE_ATTR_POINTS_COMMAND = 1606
_PET_RESET_ATTR_POINTS_COMMAND = 1565
_PET_RESET_ATTR_POINTS_ITEM_ID = 360010
_ATTR_NAMES = ('体力', '力量', '耐力', '敏捷', '智力')
_ATTR_KEYS = ('physique', 'strength', 'endurance', 'quick', 'intelligence')
# 洗五项使用面板展示的战斗派生值：生命、攻击、防御、速度、魔力。
_XD_VALUE_KEYS = ('hp_max', 'attack', 'defense', 'speed', 'mp_max')
_XD_LOWER_BOUND_INDICES = (0, 1, 3)
_XD_BASE_VALUE_KEYS = ('physique', 'strength', 'endurance', 'quick', 'intelligence')
_XD_ITEM_TYPES = ('绿色成长', '红色成长', '大丸子', '紫色五项', '红色五项')
_XD_ITEM_IDS = (350013, 360008, 360038, 350014, 360009)
_KD_VALUE_KEYS = ('grow_value', 'hp_max', 'attack', 'defense', 'speed', 'mp_max')
_KD_LOWER_BOUND_INDICES = (0, 1, 2, 4)
_PET_POSITION_NAMES = {
    1: '宠物背包',
    2: '待命',
    3: '主战',
    4: '辅助',
}

_FEATURE_LABELS = {
    1: '砸罐子',
    2: '分经验',
    3: '开书/丸子包/箱子',
    4: '背包/仓库操作',
    5: '丢仓库宠物',
    6: '洗点',
    7: '开蛋',
    8: '兑换水晶',
    9: '兑换奖牌/礼物',
    10: '一键洗点/加点',
    11: '一键升级0转99级',
}


def _normalize_enabled_features(enabled_features):
    if enabled_features is None:
        return None

    features = frozenset(enabled_features)
    invalid_features = features.difference(_FEATURE_LABELS)
    if invalid_features:
        raise ValueError('不存在的功能编号：%s' % sorted(invalid_features))
    if not features:
        raise ValueError('至少需要启用一个功能')
    return features


def _get_item_name(item_id):
    """返回 Item ID 对应的中文名，并明确标记资料中缺失的 ID。"""
    return _ITEM_NAMES.get(item_id, '未知物品（Item ID：%d）' % item_id)


def _get_pet_name(type_id):
    """返回精灵类型名称；未知类型保留 ID 便于补充映射。"""
    return _PET_NAMES.get(type_id, '未知精灵_%d' % type_id)


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


def _record_pet_result(pet, operation, uid, fwq, item_type=None):
    """将一次洗宠或开蛋结果写入 SQLite 数据库。"""
    database_path = _get_application_dir() / 'pet_results.db'
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


def login_interface(myfile='account.txt', enabled_features=None):
    global mmh, mmh_mm
    enabled_features = _normalize_enabled_features(enabled_features)
    available_features = (set(_FEATURE_LABELS) if enabled_features is None
                          else enabled_features)
    account = get_account(myfile)
    mmh_list = []
    for key in sorted(account):
        mmh_list.append(key)
    account_len = len(account)
    while account_len != 0:
        available_modes = []
        if available_features:
            available_modes.append('1.经典模式')
        if 1 in available_features:
            available_modes.append('2.一键砸罐子模式')
        if 2 in available_features:
            available_modes.append('3.一键分经验模式')
        md = int(input(['请选择模式：' + ' '.join(available_modes) + ' 0退出']))
        if md == 2 and 1 not in available_features:
            print('该精简版未启用砸罐子功能')
            continue
        if md == 3 and 2 not in available_features:
            print('该精简版未启用分经验功能')
            continue
        if md not in (1, 2, 3):
            return
        while md == 1:
            num = 0
            for x in mmh_list:
                print('%d、%s' % (num + 1, x))
                num += 1
            i = int(input(['请输入要登录的账号,0退出']))
            if i != 0 and i <= account_len:
                m = int(input(['请选择服务器：1.随机服务器 2.指定服务器']))
                mmh = int(mmh_list[i - 1])
                mmh_mm = account[mmh_list[i - 1]]
                if m == 1:
                    kaipai(mmh, mmh_mm, 1,
                           enabled_features=enabled_features)
                elif m == 2:
                    fwq = int(input(['请输入服务器']))
                    kaipai(mmh, mmh_mm, 1, fwq,
                           enabled_features=enabled_features)
                else:
                    return
            elif i > account_len:
                print('?')
            else:
                return
        if md == 2:
            print('正在一键砸罐子')
            threads = []
            for x in range(account_len):
                try:
                    thread = threading.Thread(
                        target=kaipai,
                        args=(int(mmh_list[x]), account[mmh_list[x]], 2),
                        kwargs={'enabled_features': enabled_features},
                    )
                    thread.start()
                    threads.append(thread)
                except Exception as e:
                    try:
                        print('%d砸罐子失败：%s' % (int(mmh_list[x]), e))
                    finally:
                        e = None
                        del e
            for thread in threads:
                thread.join()
            return
        elif md == 3:
            print('正在一键分经验')
            threads = []
            for x in range(account_len):
                try:
                    thread = threading.Thread(
                        target=kaipai,
                        args=(int(mmh_list[x]), account[mmh_list[x]], 3),
                        kwargs={'enabled_features': enabled_features},
                    )
                    thread.start()
                    threads.append(thread)
                except Exception as e:
                    try:
                        print('%d分经验失败：%s' % (int(mmh_list[x]), e))
                    finally:
                        e = None
                        del e
            for thread in threads:
                thread.join()
            return
        else:
            return


def get_account(myfile='account.txt'):
    with open(myfile, 'r') as file:
        content = file.read()
    account = {}
    # md5 = hashlib.md5()
    for line in content.split('\n'):
        if line:
            uid, pwd = line.split(':')
            pwd1 = hashlib.md5(pwd.encode(encoding='UTF-8')).hexdigest()
            # md5.update(pwd.encode('utf-8'))
            # pwd1 = md5.hexdigest()
            account[uid] = pwd1
    return account

def login_taomi(uid, pwd, model=1, fwq=0, return_server=False):
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

    if return_server:
        return s, s2, str2, fwq
    return s, s2, str2


def _switch_server(uid, pwd, login_socket, game_socket, current_fwq):
    """登录同组的另一台服务器，成功后关闭旧连接。"""
    server_range = range(1, 11) if current_fwq in range(1, 11) else range(11, 21)
    available_servers = [server for server in server_range if server != current_fwq]
    next_fwq = random.choice(available_servers)
    login_result = login_taomi(uid, pwd, model=1, fwq=next_fwq, return_server=True)
    if login_result is None:
        raise ConnectionError('换服登录失败')

    new_login_socket, new_game_socket, str2, next_fwq = login_result
    for old_socket in (login_socket, game_socket):
        try:
            old_socket.close()
        except OSError as exc:
            print('关闭旧服务器连接失败：%s' % exc)
    return new_login_socket, new_game_socket, str2, next_fwq


def kaipai(uid, pwd, model, fwq=0, enabled_features=None):
    enabled_features = _normalize_enabled_features(enabled_features)
    if enabled_features is not None:
        required_feature = {2: 1, 3: 2}.get(model)
        if required_feature is not None and required_feature not in enabled_features:
            raise ValueError('当前版本未启用功能：%s' % _FEATURE_LABELS[required_feature])

    s, s2, str2, current_fwq = login_taomi(uid, pwd, model, fwq, return_server=True)

    def switch_server():
        nonlocal s, s2, str2, current_fwq
        s, s2, str2, current_fwq = _switch_server(
            uid, pwd, s, s2, current_fwq)
        return s2, str2

    def get_current_fwq():
        return current_fwq

    if model == 1:
        m = 1
        visible_features = (set(_FEATURE_LABELS) if enabled_features is None
                            else enabled_features)
        menu_items = ['%d%s' % (number, _FEATURE_LABELS[number])
                      for number in sorted(visible_features)]
        menu_prompt = '请输入要使用的功能：%s,0退出' % ','.join(menu_items)
        while m != 0:
            m = int(input([menu_prompt]))
            if enabled_features is not None and m not in enabled_features and m != 0:
                print('该精简版未启用此功能')
                continue
            if m == 1:
                zgz(s2, str2)
            if m == 2:
                fjy(s2, str2, 0)
            if m == 3:
                type = int(input(['请输入要开启的物品：1经验书,2丸子包,3基姆箱子,4职业箱子,0其他']))
                if type == 1:
                    openbook(s2, str2)
                elif type == 2:
                    openbox(s2, str2, 360037)
                elif type == 3:
                    openjmbox(s2, str2, 300100)
                elif type == 4:
                    openzybox(s2, str2)
                else:
                    id = int(input(['请输入要开启的物品代码']))
                    openbox(s2, str2, id)
            if m == 4:
                bag_store_operations(s2, str2)
            if m == 5:
                fscw(s2, str2)
            if m == 6:
                xd_menu(s2, str2, switch_server, uid, get_current_fwq)
            if m == 7:
                kd(s2, str2, switch_server, uid, get_current_fwq)
            if m == 8:
                excrystal(s2, str2)
            if m == 9:
                type = int(input(['请输入要兑换的物品：1巨石碎片->奖牌,2巨石碎片->宝物,3巨石碎片->大丸子']))
                count = int(input(['请输入兑换数量']))
                exchangelb(s2, str2, type, count)
            if m == 10:
                reset_and_allocate_attr_points_menu(s2, str2)
            if m == 11:
                fpexp099(s2, str2)
            if m == 123:
                position = int(input(['请输入地点：1海滩，2草木树海，3吉普豆3号地道，4新生巨石蟹，5伊影，6克拉斯岩洞外，7平原7区，8吉普豆2号地道']))
                turn_wait, end_wait = prompt_battle_waits(position)
                battle(s2, str2, position, turn_wait, end_wait,
                       login_socket=s, reconnect_fwq=current_fwq)
            if m == 666:
                wpe_test(s2, str2)
        s.close()
        s2.close()
        print('成功退出')
    if model == 2:
        zgz(s2, str2)
        print('%d成功砸罐子翻牌' % uid)
        time.sleep(3)
        s.close()
        s2.close()
    if model == 3:
        print("开始分经验")
        fjy(s2, str2, 1)
        time.sleep(3)
        s.close()
        s2.close()
        print('%d分经验完毕' % uid)


def zgz(s, str2):
    packet = [0, 0, 0, 38, 3, 236, *str2, 0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 198, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1,
              84, 0,
              0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 168]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2, 0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 197, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 52, 0, 0, 0, 0, 0, 0, 19, 166]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 115, 0, 0, 0, 0, 0, 0, 19, 167]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 194, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 162]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 195, 0, 0, 0, 0, 0, 0, 19, 161]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 61, 0, 0, 0, 0, 0, 0, 19, 163]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 138, 0, 0, 0, 0, 0, 0, 19, 164]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 195, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 165]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 193, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 159]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 160]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 202, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 171]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 38, 3, 236, *str2,
              0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 201, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
              0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 170]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 212, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 172]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 173]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 210, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 174]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 175]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 176]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 211, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 177]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 178]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 179]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 212, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 180]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 181]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 214, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 182]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 183]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 215, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 184]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 185]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 94, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 153]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 154]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 155]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 156]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 95, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 157]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    time.sleep(2)
    packet = [0, 0, 0, 38, 3, 236, *str2,
              0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 110, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
              0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 137]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 138]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 139]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 140]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 141]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 142]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 143]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 144]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 82, 111, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 145]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 146]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 147]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 148]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 149]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 150]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 151]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 152]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 84, 247, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 241]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 242]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 243]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 244]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 245]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 246]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 247]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 248]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 83, 253, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 226]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 227]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 228]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 83, 254, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 229]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 230]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 231]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 83, 255, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 232]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 233]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 234]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 83, 153, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 237]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 238]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 239]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 240]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 137, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 211]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 212]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 213]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 214]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 215]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    time.sleep(1)
    packet = [0, 0, 0, 38, 3, 236, *str2,
              0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 139, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
              0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 218]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 219]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    time.sleep(0.5)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 143, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 224]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 225]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 138, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 216]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 217]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 142, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 223]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 141, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 221]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 222]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 140, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 220]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 144, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 235]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 53, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 186]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 187]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 188]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 54, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 189]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    time.sleep(1)
    packet = [0, 0, 0, 38, 3, 236, *str2,
              0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 55, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
              0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 192]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 193]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 194]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 57, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 195]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 196]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 197]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 198]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 58, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 199]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 200]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 201]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 62, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 202]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 203]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 204]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 205]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 83, 63, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 206]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 207]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 208]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 209]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 210]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 6, 103, 0, 0, 0, 0, 0, 0, 44, 237, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 236]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 200, 0, 0, 0, 0, 0, 0, 0, 145, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 7, 99, 0, 0, 0, 0, 0, 0, 19, 169]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    packet = [
        0, 0, 0, 38, 3, 236, *str2,
        0, 0, 5, 165, 0, 0, 0, 0, 0, 0, 43, 96, 0, 0, 0, 0, 0, 0, 1, 216, 0, 0, 1, 84, 0,
        0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('38B',), *t1)
    s.send(req)
    packet = [0, 0, 0, 22, 6, 164, *str2, 0, 0, 6, 121, 0, 0, 0, 0, 0, 0, 19, 158]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    print('砸罐子完成')
    time.sleep(0.5)
    packet = [0, 0, 0, 18, 4, 18, *str2, 0, 0, 5, 207, 0, 0, 0, 0]
    t1 = tuple(packet)
    for x in range(15):
        req = struct.pack(*('18B',), *t1)
        s.send(req)
    print('开牌成功')
    time.sleep(0.5)
    packet = [0, 0, 0, 22, 6, 172, *str2, 0, 0, 5, 141, 0, 0, 0, 0, 0, 0, 0, 1]
    t1 = tuple(packet)
    for x in range(3):
        req = struct.pack(*('22B',), *t1)
        s.send(req)
    packet = [0, 0, 0, 22, 6, 172, *str2, 0, 0, 5, 141, 0, 0, 0, 0, 0, 0, 0, 2]
    t1 = tuple(packet)
    for x in range(3):
        req = struct.pack(*('22B',), *t1)
        s.send(req)
    print('幸运卡片翻牌成功')
    time.sleep(0.5)
    packet = [0, 0, 0, 22, 6, 84, *str2, 0, 0, 5, 176, 0, 0, 0, 0, 0, 0, 140, 160]
    t1 = tuple(packet)
    for x in range(10):
        req = struct.pack(*('22B',), *t1)
        s.send(req)
    print('炼金术之路抽奖次数增加成功')
    packet = [0, 0, 0, 18, 6, 87, *str2, 0, 0, 4, 190, 0, 0, 0, 0]
    t1 = tuple(packet)
    for x in range(60):
        req = struct.pack(*('18B',), *t1)
        s.send(req)
    print('炼金术之路抽奖成功')

    time.sleep(1)
    clearbag(s, str2)
    time.sleep(1)
    cleanequipment(s, str2)


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


def fjy(s, str2, num):

    pet_data = _get_pet_bag(s, str2)
    a = pet_data['pets']
    i = pet_data['pet_count']

    if num == 0:
        for x in range(i):
            pet = a[x]
            print(
                f'共有{i}只宠物\n',
                f"您的第{x + 1}个宠物是：{pet['nick']},等级是{pet['level']},宠物所在位置为[{pet['status']}],转生次数{pet['reincarnation_degree']},"
                f"已分配经验{pet['experience']},转生所需经验{jsexp(pet['reincarnation_degree'], 0) - pet['experience']}")

    packet = [0, 0, 0, 22, 7, 208, *str2, 0, 0, 5, 179, 0, 0, 0, 0, *str2]
    req = struct.pack(*('22B',), *packet)
    s.send(req)
    rec = s.recv(2048)
    r1 = tuple(rec)
    while r1[4] * 256 + r1[5] != 2000:
        s.send(req)
        rec = s.recv(2048)
        r1 = tuple(rec)

    exp = getexp(r1[(-8):-4])
    x = 0
    if num == 0:
        print('经验树剩余经验：%d' % exp)
        a2 = input('请输入要分配经验的宠物:(按0退出)')
        x = int(a2) - 1
        if x == -1:
            return
        print('您要分配经验的宠物是%s' % a[x]['nick'])
    elif num == 1:
        b = 0
        while b < i and a[b]['reincarnation_degree'] == 10 and a[b]['level'] == 99:
            b += 1

        if b < i:
            x = b
        print('您要分配经验的宠物是%s' % a[x]['nick'])
    pet = a[x]
    exp -= yjzs(exp, jsexp(pet['reincarnation_degree'], 0) - pet['experience'], pet['pet_id_bytes'], str2, s)
    i = pet['reincarnation_degree'] + 1
    while exp > 0 and i != 11:
        exp -= yjzs(exp, jsexp(i, 0), pet['pet_id_bytes'], str2, s)
        i += 1

    if exp > 0:
        if num == 1:
            fjy(s, str2, 1)


def getname(name_str):
    list1 = [hex(i)[2:4] for i in name_str]
    list2 = '%'
    for i in range(0, len(list1)):
        if list1[i] == '0':
            list1 = list1[None:i]
            break

    list2 += '%'.join(list1)
    url_data = parse.unquote(list2)
    return url_data


def getexp(str):
    sum = 0
    for i in str:
        sum = sum * 256 + i

    return sum


def yjzs(syexp, exp, pet, uid, s):
    i = 0
    exp1 = 0
    if syexp < exp:
        exp = syexp
        i = 1
    print('目标分配经验为%d' % exp)
    exp1 = exp
    a = 0
    if exp <= 0:
        print('NT?')
    else:
        while exp > 999999:
            fpexp(999999, pet, uid, s)
            exp -= 999999
            a += 1
            if a % 10 == 0:
                time.sleep(0.25)

        fpexp(exp, pet, uid, s)
        print('分配经验完成')
    if i == 0:
        print('正在转生')
        zs(pet, uid, s)
    return exp1


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

def fscw(s, str2):
    try:
        pet_data = _get_pet_store(s, str2)
    except (ConnectionError, ValueError) as exc:
        print(exc)
        return

    pets = pet_data['pets']
    i = pet_data['pet_count']
    print('共有%d只宠物' % i)
    for index, pet in enumerate(pets, start=1):
        print('您的第%d个宠物是：%s,等级是%d' %
              (index, pet['nick'], pet['level']))

    name = input('请输入要丢弃宠物的名字')
    dj = int(input('请输入要丢弃宠物的等级'))

    count = 0
    for pet in pets:
        if pet['level'] == dj and pet['nick'] == name:
            packet = [0, 0, 0, 22, 6, 25, *str2, 0, 0, 8, 34,
                      0, 0, 0, 0, *pet['pet_id_bytes']]
            s.send(struct.pack('22B', *packet))
            count += 1
    print('放生成功，共放生%d只宠物' % count)


def _prop_backto_store(s, str2, item_id, quantity):
    """将指定数量的道具放入仓库。"""
    item_id_bytes = item_id.to_bytes(4, byteorder='big')
    quantity_bytes = quantity.to_bytes(4, byteorder='big')
    packet = [0, 0, 0, 30, 4, 99, *str2, 0, 0, 5, 15, 0, 0, 0, 0, 0, 0, 0, 1, *item_id_bytes,
              *quantity_bytes]
    t1 = tuple(packet)
    req = struct.pack(*('30B',), *t1)
    s.send(req)
    time.sleep(0.1)


def _move_equip_storage(s, str2, direction, instance_id):
    """通过1124协议在装备背包和仓库之间移动一个装备实例。"""
    if direction not in (0, 1):
        raise ValueError('装备移动方向必须是0或1')

    if isinstance(instance_id, int):
        if instance_id <= 0 or instance_id > 0xFFFFFFFF:
            raise ValueError('装备实例ID必须是有效的uint32正整数')
        instance_id_bytes = instance_id.to_bytes(4, byteorder='big')
    else:
        try:
            instance_id_bytes = bytes(instance_id)
        except (TypeError, ValueError) as exc:
            raise ValueError('装备实例ID必须是uint32或4字节序列') from exc
        if len(instance_id_bytes) != 4 or int.from_bytes(instance_id_bytes, byteorder='big') == 0:
            raise ValueError('装备实例ID必须是非零的4字节序列')

    direction_bytes = direction.to_bytes(4, byteorder='big')
    packet = [0, 0, 0, 26, 4, 100, *str2, 0, 0, 5, 15, 0, 0, 0, 0,
              *direction_bytes, *instance_id_bytes]
    req = struct.pack(*('26B',), *tuple(packet))
    s.send(req)
    time.sleep(0.1)


def _equip_backto_store(s, str2, instance_id):
    """将指定实例的背包装备放入仓库。"""
    _move_equip_storage(s, str2, 1, instance_id)


def _fetch_equip_from_store(s, str2, instance_id):
    """将指定实例的仓库装备取回背包。"""
    _move_equip_storage(s, str2, 0, instance_id)


def _prop_sell(s, str2, item_id, quantity):
    """出售指定数量的道具。"""
    item_id_bytes = item_id.to_bytes(4, byteorder='big')
    quantity_bytes = quantity.to_bytes(4, byteorder='big')
    packet = [0, 0, 0, 30, 4, 88, *str2, 0, 0, 5, 115, 0, 0, 0, 0, 0, 0, 0, 1, *item_id_bytes,
              *quantity_bytes]
    t1 = tuple(packet)
    req = struct.pack(*('30B',), *t1)
    s.send(req)
    time.sleep(0.1)


def _equipment_sell(s, str2, item_id, instance_id):
    """出售指定实例的装备。"""
    packet = [0, 0, 0, 26, 4, 87, *str2, 0, 0, 5, 4, 0, 0, 0, 0, 0, 0, 0, 1, *instance_id]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)
    print('装备“%s”已出售' % _get_item_name(item_id))
    time.sleep(0.1)


def _equipment_discard(s, str2, item_id, instance_id):
    """丢弃指定实例的装备。"""
    packet = [0, 0, 0, 22, 4, 80, *str2, 0, 0, 6, 81, 0, 0, 0, 0, *instance_id]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    print('装备“%s”已丢弃' % _get_item_name(item_id))
    time.sleep(0.1)


def _repair_equipment(s, str2):
    """修理人物当前装备栏中的全部装备。"""
    user_info = _get_more_userinfo(s, str2)
    for equipment in user_info['suit_items']:
        instance_id = bytes(equipment['instance_id'])
        item_id = int(equipment['item_id']).to_bytes(4, byteorder='big')
        if len(instance_id) != 4:
            raise ValueError('装备实例ID必须是4字节序列')

        packet = [
            0, 0, 0, 0x1E, 0x04, 0x5F, *str2,
            0, 0, random.randint(5, 6), random.randint(0, 255),
            0, 0, 0, 0, 0, 0, 0, 0,
            *instance_id, *item_id,
        ]
        s.send(struct.pack('30B', *packet))
        time.sleep(0.05)


def bag_store_operations(s, str2):
    """处理背包与仓库之间的手动道具、装备转移。"""
    while True:
        try:
            operation = int(input(
                '请选择操作：1.背包道具->仓库，2.仓库->背包道具，'
                '3.背包装备->仓库，4.仓库->背包装备，0.返回\n'))
        except ValueError:
            print('操作编号必须是整数')
            continue

        if operation == 0:
            return
        _run_bag_store_operation(s, str2, operation)


def _run_bag_store_operation(s, str2, operation):
    """执行一次背包或仓库操作，完成后由上层菜单继续接收选择。"""
    if operation not in (1, 2, 3, 4):
        print('无效的操作编号')
        return

    if operation in (3, 4):
        if operation == 3:
            equipment_info, _ = _get_equipment_bag_info(s, str2)
            source_name = '背包'
            empty_message = '背包中没有装备'
            input_message = '请输入要放入仓库的装备实例ID：'
            move_equipment = _equip_backto_store
            success_message = '%s已放入仓库'
        else:
            equipment_info, _ = _get_equip_store_info(s, str2)
            source_name = '仓库'
            empty_message = '仓库中没有装备'
            input_message = '请输入要从仓库拿出的装备实例ID：'
            move_equipment = _fetch_equip_from_store
            success_message = '%s已从仓库拿出'

        if not equipment_info:
            print(empty_message)
            return

        print('%s装备：' % source_name)
        for equipment in equipment_info:
            instance_id = int.from_bytes(
                bytes(equipment['instance_id']), byteorder='big')
            print('道具ID：%d，实例ID：%d，名称：%s' % (
                equipment['item_id'], instance_id,
                _get_item_name(equipment['item_id'])))

        try:
            selected_instance_id = int(input(input_message))
        except ValueError:
            print('装备实例ID必须是整数')
            return

        selected_equipment = next((
            equipment for equipment in equipment_info
            if int.from_bytes(bytes(equipment['instance_id']), byteorder='big')
            == selected_instance_id
        ), None)
        if selected_equipment is None:
            print('%s中不存在实例ID为%d的装备' % (
                source_name, selected_instance_id))
            return

        move_equipment(s, str2, selected_equipment['instance_id'])
        print(success_message % _get_item_name(selected_equipment['item_id']))
        return

    if operation == 2:
        prop_info, _ = _get_prop_store_info(s, str2)
        if not prop_info:
            print('仓库中没有道具')
            return

        print('仓库道具：')
        for prop in prop_info:
            print('ID：%d，名称：%s，数量：%d' % (
                prop['item_id'], _get_item_name(prop['item_id']), prop['quantity']))

        try:
            item_id = int(input('请输入要从仓库拿出的道具ID：'))
            quantity = int(input('请输入要从仓库拿出的数量：'))
        except ValueError:
            print('道具ID和数量必须是整数')
            return

        available_quantity = sum(
            prop['quantity'] for prop in prop_info if prop['item_id'] == item_id)
        if available_quantity == 0:
            print('仓库中不存在ID为%d的道具' % item_id)
            return
        if quantity <= 0:
            print('拿出数量必须大于0')
            return
        if quantity > available_quantity:
            print('拿出数量不能超过仓库持有数量%d' % available_quantity)
            return

        _fetch_item_from_store(s, str2, item_id, quantity)
        print('%s已从仓库拿出，共%d个' % (_get_item_name(item_id), quantity))
        return

    prop_info, _ = _get_prop_bag_info(s, str2)
    if not prop_info:
        print('背包中没有道具')
        return

    print('背包道具：')
    for prop in prop_info:
        print('ID：%d，名称：%s，数量：%d' % (
            prop['item_id'], _get_item_name(prop['item_id']), prop['quantity']))

    try:
        item_id = int(input('请输入要放入仓库的道具ID：'))
        quantity = int(input('请输入要放入仓库的数量：'))
    except ValueError:
        print('道具ID和数量必须是整数')
        return

    available_quantity = sum(
        prop['quantity'] for prop in prop_info if prop['item_id'] == item_id)
    if available_quantity == 0:
        print('背包中不存在ID为%d的道具' % item_id)
        return
    if quantity <= 0:
        print('放入数量必须大于0')
        return
    if quantity > available_quantity:
        print('放入数量不能超过背包持有数量%d' % available_quantity)
        return

    _prop_backto_store(s, str2, item_id, quantity)
    print('%s已放入仓库，共%d个' % (_get_item_name(item_id), quantity))


def clearbag(s, str2):
    print('正在清理背包')
    prop_info, _ = _get_prop_bag_info(s, str2)
    for prop in prop_info:
        inid = prop['item_id']
        if (inid >= 310000 and inid < 320000) or (inid >= 210000 and inid <= 210011) or inid in (370000, 370002, 370005,
                                                                                                 290011, 290012, 290013,
                                                                                                 300044, 300045, 300046,
                                                                                                 300080, 200002, 180041,
                                                                                                 180042, 230012,
                                                                                                 300002):
            _prop_backto_store(s, str2, inid, prop['quantity'])
            print('%s已放入仓库' % _get_item_name(inid))
        if (inid >= 200004 and inid < 210000) or inid in (180043, 180044):
            _prop_sell(s, str2, inid, prop['quantity'])
            print('%s已出售' % _get_item_name(inid))

    print('清理完毕')


def _byte_to_int(data, scale=4):
    return int.from_bytes(data[:scale], byteorder="big")


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


def _get_equipment_bag_info(s, str2):
    """
    获取并解析背包内装备信息。
    :param s: socket连接
    :param str2: 米米号
    :return: 结构化装备信息列表和装备数量
    """
    packet = [0, 0, 0, 18, 4, 78, *str2, 0, 0, 4, 201, 0, 0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('18B',), *t1)
    s.send(req)

    response = _receive_bag_response(s, str2, 1102, '装备列表')
    offset = 18

    if offset + 4 > len(response):
        raise ValueError('%s获取装备列表失败：缺少装备数量' % str2)
    equipment_count = struct.unpack_from('>I', response, offset)[0]
    offset += 4
    if equipment_count > (len(response) - offset) // 96:
        raise ValueError('%s获取装备列表失败：装备数量与包长不匹配' % str2)

    equipment_info = []
    for _ in range(equipment_count):
        item, offset = _parse_equipment_record(response, offset, str2)
        equipment_info.append(item)

    if offset != len(response):
        raise ValueError('%s获取装备列表失败：包尾存在未解析数据' % str2)
    return equipment_info, equipment_count


def _get_equip_store_info(s, str2):
    """
    获取并解析仓库内的全部装备信息。
    :param s: socket连接
    :param str2: 米米号
    :return: 结构化装备信息列表和仓库装备总数
    """
    page_size = 360
    start_index = 0
    total_count = None
    equipment_info = []

    while True:
        start_index_bytes = start_index.to_bytes(4, byteorder='big')
        page_size_bytes = page_size.to_bytes(4, byteorder='big')
        packet = [0, 0, 0, 26, 4, 98, *str2, 0, 0, 4, 233, 0, 0, 0, 0,
                  *start_index_bytes, *page_size_bytes]
        s.send(struct.pack(*('26B',), *tuple(packet)))

        response = _receive_bag_response(s, str2, 1122, '装备仓库列表')
        offset = 18
        if len(response) - offset < 12:
            raise ValueError('%s获取装备仓库列表失败：缺少分页信息' % str2)

        response_total, response_start, page_count = struct.unpack_from(
            '>III', response, offset)
        offset += 12

        if response_start != start_index:
            raise ValueError('%s获取装备仓库列表失败：分页起始位置不匹配' % str2)
        if total_count is None:
            total_count = response_total
        elif response_total != total_count:
            raise ValueError('%s获取装备仓库列表失败：分页总数不一致' % str2)
        if response_start > total_count or page_count > total_count - response_start:
            raise ValueError('%s获取装备仓库列表失败：分页数量超出装备总数' % str2)
        if page_count > (len(response) - offset) // 96:
            raise ValueError('%s获取装备仓库列表失败：装备数量与包长不匹配' % str2)

        for _ in range(page_count):
            item, offset = _parse_equipment_record(response, offset, str2)
            equipment_info.append(item)

        if offset != len(response):
            raise ValueError('%s获取装备仓库列表失败：包尾存在未解析数据' % str2)

        next_index = response_start + page_size
        if total_count <= next_index:
            break
        if page_count == 0:
            raise ValueError('%s获取装备仓库列表失败：分页未返回装备数据' % str2)
        start_index = next_index

    if len(equipment_info) != total_count:
        raise ValueError('%s获取装备仓库列表失败：实际装备数量与总数不一致' % str2)
    return equipment_info, total_count


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


def cleanequipment(s, str2):
    print('正在清理装备')
    equipment_info, num = _get_equipment_bag_info(s, str2)

    for x in range(num):
        item = equipment_info[x]
        inid = item['item_id']

        if (inid >= 80074 and inid <= 80077) or (inid >= 130005 and inid <= 130006):
            _equipment_discard(s, str2, inid, item['instance_id'])    
        
        if (inid >= 130008 and inid <= 130011) or (inid >= 120001 and inid <= 120004) or (
                inid >= 130001 and inid <= 130004):
            _equipment_sell(s, str2, inid, item['instance_id'])
        
        # 丢弃绅士套/淑女套
        if (inid >= 80070 and inid <= 80077):
            _equipment_discard(s, str2, inid, item['instance_id'])
                
    print('装备清理完成')

def openzybox(s, str2):
    zhiye = input(['请输入要开箱的职业拼音首字母，如 剑士:js'])
    if zhiye == 'sy':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 75]
        weapon_id = 142040
        shoes_id = [142036, 142037, 142038, 142039]
    elif zhiye == 'cj':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 71]
        weapon_id = 140170
        shoes_id = [140164, 140166, 140167, 140168]
    elif zhiye == 'js':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 69]
        weapon_id = 140161
        shoes_id = [140157, 140158, 140159, 140160, 140162]
    elif zhiye == 'gj':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 70]
        weapon_id = 140169
        shoes_id = [140163, 140165, 140167, 140168]
    elif zhiye == 'rz':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 72]
        weapon_id = 140303
        shoes_id = [140301, 140302, 140167, 140168]
    elif zhiye == 'kz':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 73]
        weapon_id = 141040
        shoes_id = [141036, 141037, 141038, 141039]
    elif zhiye == 'hm':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 74]
        weapon_id = 141540
        shoes_id = [141536, 141537, 141538, 141539]
    elif zhiye == 'ws':
        packet_xz = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 212, 0, 0, 0, 0, 0, 4, 148, 76]
        weapon_id = 142540
        shoes_id = [142536, 142537, 142538, 142539]
    else:
        return
    expect = input(['请输入期望的数值(攻击 魔攻 精神/恢复 速度 防御),不追求的输入0'])
    expect = expect.split(' ')
    expect = [int(x) for x in expect]
    con = ''
    while con == '':
        for i in range(8):
            t1 = tuple(packet_xz)
            req = struct.pack(*('22B',), *t1)
            s.send(req)

        equipment_info, num = _get_equipment_bag_info(s, str2)

        weapon = 0
        shoes = 0
        expect_weapon = 0
        expect_shoes = 0
        if zhiye == 'sy':
            for item in equipment_info:
                inid = item['item_id']
                if inid == weapon_id:
                    weapon += 1
                    print('您的第%d把武器攻击为%d，魔攻为%d，恢复力为%d' % (
                        weapon, item['attack'], item['magic_attack'], item['recovery']))
                    if item['attack'] >= expect[0] and item['magic_attack'] >= expect[1] and item['recovery'] >= expect[2]:
                        expect_weapon += 1
        elif zhiye in ['cj', 'hm']:
            for item in equipment_info:
                inid = item['item_id']
                if inid == weapon_id:
                    weapon += 1
                    print('您的第%d把武器攻击为%d，魔攻为%d，精神为%d' % (
                        weapon, item['attack'], item['magic_attack'], item['spirit']))
                    if item['attack'] >= expect[0] and item['magic_attack'] >= expect[1] and item['spirit'] >= expect[2]:
                        expect_weapon += 1
        elif zhiye in ['js', 'gj', 'kz', 'rz', 'ws']:
            for item in equipment_info:
                inid = item['item_id']
                if inid == weapon_id:
                    weapon += 1
                    print('您的第%d把武器攻击为%d' % (weapon, item['attack']))
                    if item['attack'] >= expect[0]:
                        expect_weapon += 1
        else:
            return
        print('共有%d件武器符合要求' % expect_weapon)

        if not zhiye in ['js', 'kz']:
            for item in equipment_info:
                inid = item['item_id']
                if inid == shoes_id[-1]:
                    shoes += 1
                    print('您的第%d双鞋子速度为%d,防御为%d' % (
                        shoes, item['speed'], item['defense']))
                    if item['speed'] >= expect[3] and item['defense'] >= expect[4]:
                        expect_shoes += 1
        print('共有%d双鞋子符合要求' % expect_shoes)

        if expect_weapon != 0 or expect_shoes != 0:
            m = int(input(['是否清理背包? 1.清理 0.退出']))
            if m == 1:
                for item in equipment_info:
                    inid = item['item_id']
                    if (inid in shoes_id) or (inid == weapon_id):
                        _equipment_sell(s, str2, inid, item['instance_id'])
                print('清理完毕')
            elif m == 0:
                return
            con = input(['是否继续? 回车继续 0.退出'])
        else:
            for item in equipment_info:
                inid = item['item_id']
                if (inid in shoes_id) or (inid == weapon_id):
                    _equipment_sell(s, str2, inid, item['instance_id'])
            print('清理完毕')


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


def excrystal(s, str2):
    md = int(input(['请输入水晶兑换的物品:1基姆箱子,2五项丸子']))
    if md == 1:
        item_index = 0x46
    elif md == 2:
        item_index = 0x48
    else:
        return
    quantity = int(input(['请输入兑换的数量']))
    _exchange_item(s, str2, 0x27, item_index, quantity)
    print('兑换成功')


_JMBOX_BATCH_SIZE = 99
_MAGIC_CRYSTAL_ID = 290012
_JMBOX_EXCHANGE_NPC_ID = 0x27
_JMBOX_EXCHANGE_ITEM_INDEX = 0x46
_EXCHANGE_BLESS_GIFT_COMMAND = 1127
_JMBOX_REWARD_ITEM_IDS = (
    360040, 350051, 350050, 370002, 370012, 370022, 370032, 370042,
    370052, 300101, 300102, 300103, 300104, 300105, 300106, 300107,
    300108,
)
_JMBOX_TARGET_ITEM_IDS = frozenset(range(300101, 300109))


def _parse_exchange_bless_gift_response(packet):
    """解析 1127 回包，返回错误码和服务端给出的奖励信息。

    反编译的 ExchangeItemOpenGiftBoxProtocol 依次读取：
    exchangeRandomID(int)、奖励数量(uint)、以及每个奖励的 goodID(uint)。
    TMF 头部 14..17 字节是错误码；失败时通常只有 18 字节头部。
    """
    if len(packet) < _TMF_HEADER_SIZE:
        raise ValueError('1127协议回包头不完整')

    packet_length, command_id = struct.unpack_from('>IH', packet)
    if packet_length != len(packet):
        raise ValueError(
            '1127协议回包长度不一致（声明%d，实际%d）' %
            (packet_length, len(packet)))
    if command_id != _EXCHANGE_BLESS_GIFT_COMMAND:
        raise ValueError(
            '协议命令不匹配（期望%d，实际%d）' %
            (_EXCHANGE_BLESS_GIFT_COMMAND, command_id))

    error_code = struct.unpack_from('>I', packet, 14)[0]
    if error_code:
        if len(packet) != _TMF_HEADER_SIZE:
            raise ValueError('1127协议错误回包不应包含额外负载')
        return {
            'success': False,
            'error_code': error_code,
            'exchange_random_id': None,
            'item_ids': (),
        }

    payload = packet[_TMF_HEADER_SIZE:]
    if len(payload) < 8:
        raise ValueError('1127协议成功回包负载不完整')

    exchange_random_id, item_count = struct.unpack_from('>iI', payload)
    expected_payload_size = 8 + item_count * 4
    if len(payload) != expected_payload_size:
        raise ValueError(
            '1127协议奖励数量与负载长度不一致（数量%d，负载%d）' %
            (item_count, len(payload)))

    item_ids = (struct.unpack_from('>%dI' % item_count, payload, 8)
                if item_count else ())
    return {
        'success': True,
        'error_code': 0,
        'exchange_random_id': exchange_random_id,
        'item_ids': item_ids,
    }


def openbox(s, str2, id=300100):
    id = hex(id)[2:].zfill(6)
    packet = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 251, 0, 0, 0, 0, 0, int(id[0:2], base=16), int(id[2:4], base=16),
              int(id[4:6], base=16)]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    num = int(input('请输入开启数量'))
    for i in range(num):
        s.send(req)
        if i % 50 == 0:
            time.sleep(0.5)


def openjmbox(s, str2, id=300100):
    """兑换并逐个开启吉姆宝箱，失败开箱不消耗本地箱子计数。"""
    print("说明：自动兑换吉姆宝箱并开启，可以通过堆满背包的方式卡职业箱子。\n使用方法：使用前清空收集品背包和可消耗品背包；在收集品中放入99个魔力水晶，并用其他吉姆宝箱中无法开出的物品填充满；在可消耗品中放入1个需要卡的职业箱子，留一个空位，然后其余用吉姆宝箱中无法开出的物品填充满\n")
    try:
        target_quantity = int(input('请输入要成功开启的数量'))
    except ValueError:
        print('开启数量必须是整数')
        return
    if target_quantity <= 0:
        print('开启数量必须大于0')
        return

    item_id_bytes = int(id).to_bytes(4, byteorder='big')
    packet = [0, 0, 0, 22, 4, 103, *str2, 0, 0, 5, 251, 0, 0, 0, 0,
              *item_id_bytes]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)

    opened_quantity = 0
    attempt_quantity = 0
    remaining_boxes = 0
    while opened_quantity < target_quantity:
        if remaining_boxes == 0:
            # _fetch_item_from_store(
            #     s, str2, _MAGIC_CRYSTAL_ID, _JMBOX_BATCH_SIZE)
            _exchange_item(
                s, str2, _JMBOX_EXCHANGE_NPC_ID,
                _JMBOX_EXCHANGE_ITEM_INDEX, _JMBOX_BATCH_SIZE)
            _fetch_item_from_store(
                s, str2, _MAGIC_CRYSTAL_ID, _JMBOX_BATCH_SIZE)
            # _fetch_item_from_store(
            #     s, str2, 300100, _JMBOX_BATCH_SIZE)
            remaining_boxes = _JMBOX_BATCH_SIZE

        s.send(req)
        response = _receive_bag_response(
            s, str2, _EXCHANGE_BLESS_GIFT_COMMAND, '吉姆宝箱开启结果')
        result = _parse_exchange_bless_gift_response(response)
        attempt_quantity += 1

        if result['success']:
            reward_index = result['exchange_random_id']
            reward_item_id = None
            if 0 <= reward_index < len(_JMBOX_REWARD_ITEM_IDS):
                reward_item_id = _JMBOX_REWARD_ITEM_IDS[reward_index]
                reward_name = _get_item_name(reward_item_id)
            else:
                reward_name = '未知奖励'
            opened_quantity += 1
            remaining_boxes -= 1
            print(
                '第%d次开箱：成功，获得%s，已成功开启%d/%d，'
                '本批宝箱剩余%d个' %
                (attempt_quantity, reward_name,
                 opened_quantity, target_quantity, remaining_boxes))

            if reward_item_id in _JMBOX_TARGET_ITEM_IDS:
                _prop_backto_store(s, str2, reward_item_id, 1)
                print('%s已立即放回仓库1个' % _get_item_name(reward_item_id))
        else:
            print(
                '第%d次开箱：失败，已成功开启%d/%d，'
                '本批宝箱剩余%d个' %
                (attempt_quantity, opened_quantity,
                 target_quantity, remaining_boxes))


def openbook(s, str2):    
    packet = [0, 0, 0, 26, 4, 105, *str2, 0, 0, 5, 149, 0, 0, 0, 0, 0, 5, 87, 98, 0, 0, 0, 1]
    t1 = tuple(packet)
    req = struct.pack('26B', *t1)
    num = int(input('请输入开启数量'))
    _fetch_item_from_store(s, str2, 350050, num)
    for i in range(num):
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


def _parse_role_reset_attr_points_response(packet):
    """解析 1115：角色/宠物 ID、剩余点和当前 HP/MP。"""
    payload = _parse_tmf_response_payload(
        packet, _RESET_ATTR_POINTS_COMMAND)
    expected_size = struct.calcsize('>IHIIII')
    if len(payload) != expected_size:
        raise ValueError(
            '1115协议负载长度错误（期望%d，实际%d）' %
            (expected_size, len(payload)))

    pet_id, remain_points, hp, mp, hp_max, mp_max = struct.unpack(
        '>IHIIII', payload)
    return {
        'pet_id': pet_id,
        'remain_points': remain_points,
        'hp': hp,
        'mp': mp,
        'hp_max': hp_max,
        'mp_max': mp_max,
    }


def _parse_role_allocate_attr_points_response(packet):
    """解析 1027 的精简或扩展回包。"""
    payload = _parse_tmf_response_payload(
        packet, _ROLE_ALLOCATE_ATTR_POINTS_COMMAND)
    compact_format = '>6H'
    extended_format = '>6HII9H'
    compact_size = struct.calcsize(compact_format)
    extended_size = struct.calcsize(extended_format)
    if len(payload) not in (compact_size, extended_size):
        raise ValueError(
            '1027协议负载长度错误（期望%d或%d，实际%d）' %
            (compact_size, extended_size, len(payload)))

    values = struct.unpack_from(compact_format, payload)
    result = dict(zip(_ATTR_NAMES, values[:5]))
    result['剩余点数'] = values[5]
    if len(payload) == extended_size:
        extended_values = struct.unpack(extended_format, payload)
        result.update({
            'hp_max': extended_values[6],
            'mp_max': extended_values[7],
            'attack': extended_values[8],
            'defense': extended_values[9],
            'speed': extended_values[10],
            'spirit': extended_values[11],
            'resume': extended_values[12],
            'hit_rate': extended_values[13],
            'avoid_rate': extended_values[14],
            'critical': extended_values[15],
            'fight_back': extended_values[16],
        })
    return result


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


def _prompt_attr_points(subject, max_value, max_total=None):
    while True:
        raw_value = input(
            '请输入%s加点（体力 力量 耐力 敏捷 智力，用空格分隔）：' %
            subject).strip()
        try:
            points = tuple(int(value) for value in raw_value.split())
        except ValueError:
            print('五项加点必须全部是整数')
            continue
        if len(points) != len(_ATTR_NAMES):
            print('请输入5个用空格分隔的数值')
            continue
        if any(value < 0 or value > max_value for value in points):
            print('每项加点必须在0到%d之间' % max_value)
            continue
        if max_total is not None and sum(points) > max_total:
            print('加点总和%d超过洗点后的可分配点%d，请重新输入' %
                  (sum(points), max_total))
            continue
        return points


def _reset_and_allocate_role_attr_points(s, str2):
    points = _prompt_attr_points('人物', 0xffffffff)
    reset_payload = struct.pack(
        '>II', 0, _ROLE_RESET_ATTR_POINTS_ITEM_ID)

    try:
        reset_packet = _send_tmf_request(
            s, str2, _RESET_ATTR_POINTS_COMMAND, reset_payload)
        reset_result = _parse_role_reset_attr_points_response(reset_packet)
    except (socket.timeout, ConnectionError, OSError, ValueError,
            RuntimeError) as exc:
        print('人物洗点失败：%s' % exc)
        return

    if reset_result['pet_id'] != 0:
        print('人物洗点失败：1115回包返回了宠物ID %d' %
              reset_result['pet_id'])
        return

    remain_points = reset_result['remain_points']
    print('人物洗点成功，可分配点数：%d' % remain_points)
    if sum(points) > remain_points:
        print('原加点总和%d超过可分配点，请按实际点数重新输入' % sum(points))
        points = _prompt_attr_points(
            '人物', 0xffffffff, max_total=remain_points)

    allocate_payload = struct.pack('>5I', *points)
    try:
        allocate_packet = _send_tmf_request(
            s, str2, _ROLE_ALLOCATE_ATTR_POINTS_COMMAND,
            allocate_payload)
    except (socket.timeout, ConnectionError, OSError) as exc:
        print('人物洗点已成功，但加点请求失败：%s' % exc)
        return

    try:
        result = _parse_role_allocate_attr_points_response(
            allocate_packet)
    except RuntimeError as exc:
        print('人物洗点已成功，但服务端拒绝加点：%s' % exc)
        return
    except ValueError as exc:
        print('人物加点回包解析失败，服务端可能已完成加点：%s' % exc)
        return

    attrs = '，'.join('%s%d' % (name, result[name])
                     for name in _ATTR_NAMES)
    print('人物加点成功：%s，剩余点数%d' % (attrs, result['剩余点数']))


def _select_pet_for_attr_points(s, str2):
    try:
        pet_data = _get_pet_bag(s, str2)
    except (socket.timeout, ConnectionError, OSError, ValueError) as exc:
        print('获取宠物列表失败：%s' % exc)
        return None

    pets = pet_data['pets']
    if not pets:
        print('宠物背包中没有可选择的宠物')
        return None

    print('共有%d只宠物' % len(pets))
    for index, pet in enumerate(pets, start=1):
        attrs = '，'.join(
            '%s%d' % (name, pet[key])
            for name, key in zip(_ATTR_NAMES, _ATTR_KEYS))
        print('%d.%s（等级%d，%s，%s，剩余点数%d）' % (
            index, pet['nick'], pet['level'], pet['status'], attrs,
            pet['attr_point_remaid']))

    while True:
        raw_value = input('请选择要洗点/加点的宠物，输入0返回：').strip()
        try:
            pet_number = int(raw_value)
        except ValueError:
            print('宠物编号必须是整数')
            continue
        if pet_number == 0:
            return None
        if 1 <= pet_number <= len(pets):
            return pets[pet_number - 1]
        print('宠物编号不存在')


def _reset_and_allocate_pet_attr_points(s, str2):
    selected_pet = _select_pet_for_attr_points(s, str2)
    if selected_pet is None:
        return

    expected_total = (selected_pet['attr_point_remaid'] +
                      selected_pet['attr_point_applied'])
    points = _prompt_attr_points(
        '宠物', 0xffff, max_total=expected_total)
    pet_id = selected_pet['pet_id']
    reset_payload = struct.pack(
        '>II', pet_id, _PET_RESET_ATTR_POINTS_ITEM_ID)

    try:
        reset_packet = _send_tmf_request(
            s, str2, _PET_RESET_ATTR_POINTS_COMMAND, reset_payload)
        reset_pet = _parse_pet_attr_points_response(
            reset_packet, _PET_RESET_ATTR_POINTS_COMMAND)
    except (socket.timeout, ConnectionError, OSError, ValueError,
            RuntimeError) as exc:
        print('宠物“%s”洗点失败：%s' % (selected_pet['nick'], exc))
        return

    if reset_pet['pet_id'] != pet_id:
        print('宠物洗点失败：1565回包宠物ID不匹配（期望%d，实际%d）' %
              (pet_id, reset_pet['pet_id']))
        return

    remain_points = reset_pet['attr_point_remaid']
    print('宠物“%s”洗点成功，可分配点数：%d' %
          (reset_pet['nick'], remain_points))
    if sum(points) > remain_points:
        print('原加点总和%d超过服务端返回的可分配点，请重新输入' % sum(points))
        points = _prompt_attr_points(
            '宠物', 0xffff, max_total=remain_points)

    # 反编译客户端以 ByteArray 写入 petId:uint32 + 5 个 uint16。
    allocate_payload = struct.pack('>I5H', pet_id, *points)
    try:
        allocate_packet = _send_tmf_request(
            s, str2, _PET_ALLOCATE_ATTR_POINTS_COMMAND,
            allocate_payload)
        result_pet = _parse_pet_attr_points_response(
            allocate_packet, _PET_ALLOCATE_ATTR_POINTS_COMMAND)
    except (socket.timeout, ConnectionError, OSError, ValueError,
            RuntimeError) as exc:
        print('宠物“%s”洗点已成功，但加点失败：%s' %
              (reset_pet['nick'], exc))
        return

    if result_pet['pet_id'] != pet_id:
        print('宠物加点失败：1606回包宠物ID不匹配（期望%d，实际%d）' %
              (pet_id, result_pet['pet_id']))
        return

    attrs = '，'.join(
        '%s%d' % (name, result_pet[key])
        for name, key in zip(_ATTR_NAMES, _ATTR_KEYS))
    print('宠物“%s”加点成功：%s，剩余点数%d' % (
        result_pet['nick'], attrs, result_pet['attr_point_remaid']))


def reset_and_allocate_attr_points_menu(s, str2):
    choice = input(
        '请选择洗点/加点类型：1.人物洗点/加点，2.宠物洗点/加点，0.返回：').strip()
    if choice == '1':
        _reset_and_allocate_role_attr_points(s, str2)
    elif choice == '2':
        _reset_and_allocate_pet_attr_points(s, str2)
    elif choice != '0':
        print('洗点/加点类型不存在')


def _reset_xd_state():
    """清理一次洗点流程的临时状态，避免下一次洗点继承上次目标。"""
    global cz, wx, xd_count, xd_max_count, xd_mode
    cz = 0
    wx = [0, 0, 0, 0, 0]
    xd_count = 0
    xd_max_count = 0
    xd_mode = None


def _is_threshold_target_reached(current, target, lower_bound_indices, upper_bound_indices):
    """按目标中的上下限比较数值；目标为 0 的项不参与比较。"""
    for index in lower_bound_indices:
        if target[index] != 0 and current[index] < target[index]:
            return False
    for index in upper_bound_indices:
        if target[index] != 0 and current[index] > target[index]:
            return False
    return True


def _is_xd_target_reached(pet, target, mode='derived'):
    """判断洗点目标；支持五项或成长加五项两种目标格式。"""
    if len(target) == 6:
        if mode == 'base':
            current = (pet['grow_value'],) + tuple(
                pet[key] for key in _XD_BASE_VALUE_KEYS)
            lower_bound_indices = (0, 1, 2, 4)
            upper_bound_indices = (3, 5)
        else:
            # 派生属性模式沿用开蛋目标的上下限方向。
            current = tuple(pet[key] for key in _KD_VALUE_KEYS)
            lower_bound_indices = _KD_LOWER_BOUND_INDICES
            upper_bound_indices = (3, 5)
    else:
        if mode == 'base':
            current = tuple(pet[key] for key in _XD_BASE_VALUE_KEYS)
            lower_bound_indices = (0, 1, 3)
            upper_bound_indices = (2, 4)
        else:
            current = tuple(pet[key] for key in _XD_VALUE_KEYS)
            lower_bound_indices = _XD_LOWER_BOUND_INDICES
            upper_bound_indices = (2, 4)
    return _is_threshold_target_reached(
        current, target, lower_bound_indices, upper_bound_indices)


def _is_kd_target_reached(pet, target):
    """判断开蛋目标；target 为成长、生命值、攻击力、防御力、速度、魔力。"""
    current = tuple(pet[key] for key in _KD_VALUE_KEYS)
    return _is_threshold_target_reached(
        current, target, _KD_LOWER_BOUND_INDICES, (3, 5))


def _get_continue_action(prompt):
    """读取继续、返回或换服操作。"""
    while True:
        choice = input(prompt).strip()
        if choice in ('', '0', '886'):
            return choice
        print('请直接按回车继续，输入886换服继续，或输入0返回')


def _get_xd_continue_action():
    return _get_continue_action(
        '[按回车使用相同设置重试，输入886换服继续，按0返回]')


def xd_menu(s, str2, switch_server, uid, get_current_fwq):
    """洗点子菜单；每次结束后重新选择宠物，输入 0 返回主功能菜单。"""
    session = {'game_socket': s, 'str2': str2}

    def switch_and_update_session():
        session['game_socket'], session['str2'] = switch_server()
        return session['game_socket'], session['str2']

    while not xd(session['game_socket'], session['str2'], -1, -1,
                 switch_and_update_session, uid, get_current_fwq):
        pass


def xd(s, str2, xz, num, switch_server, uid, get_current_fwq):
    global cz
    global wx
    global xd_count
    global xd_max_count
    global xd_mode

    # xz == -1 只出现在一次新的洗点流程开始时；递归刷新宠物数据时保留状态。
    if xz == -1:
        _reset_xd_state()
    pending_result = False
    item_quantities = None

    while True:
        pet_data = _load_xd_pets(s, str2)
        a = pet_data['pets']

        if xz == -1:
            print('共有%d只宠物' % pet_data['pet_count'])
            for x in range(len(a)):
                listed_pet = a[x]
                print('您的第%d个宠物是：%s,等级是%d,转生次数%d,已分配经验%d,转生所需经验%d' % (
                    x + 1, listed_pet['nick'], listed_pet['level'], listed_pet['reincarnation_degree'],
                    listed_pet['experience'],
                    jsexp(listed_pet['reincarnation_degree'], 0) - listed_pet['experience']))

            pet_number = int(input('[请选择要洗点的精灵，按0退出]'))
            if pet_number == 0:
                _reset_xd_state()
                return True
            if pet_number < 1 or pet_number > len(a):
                print('宠物编号不存在')
                continue
            xz = pet_number - 1

        pet = a[xz]
        if pending_result:
            _record_pet_result(
                pet, '洗宠', uid, get_current_fwq(), _XD_ITEM_TYPES[num])
            pending_result = False
        print('成长%s\n体力%s\t生命值%s\n力量%s\t攻击力%s\n耐力%s\t防御%s\n敏捷%s\t速度%s\n智力%s\t魔力%s' % (
            pet['grow_value'], pet['physique'], pet['hp_max'], pet['strength'], pet['attack'],
            pet['endurance'], pet['defense'], pet['quick'], pet['speed'], pet['intelligence'], pet['mp_max']))

        if item_quantities is None:
            item_quantities = _get_xd_item_quantities(s, str2)

        if num == -1:
            num = int(input('选择丸子1绿色成长2红色成长3大丸子4紫色五项5红色五项(按0退出)')) - 1
            if num == -1:
                _reset_xd_state()
                return False

        if num == 0 or num == 1:
            if cz == 0:
                cz = int(input(['请输入目标成长']))
            if pet['grow_value'] >= cz:
                print('洗成长成功，一共洗点%d次' % xd_count)
                _reset_xd_state()
                return False
            eatwz(s, str2, pet, num, item_quantities)
            pending_result = True
            continue

        if num == 3 or num == 4 or num == 2:
            # 用次数是否已设置判断是否为首次进入，允许目标全部填写 0。
            if xd_max_count == 0:
                if xd_mode is None:
                    mode_choice = input(
                        '请选择判断属性：1.体力/力量/耐力/敏捷/智力，'
                        '2.生命值/攻击力/防御力/速度/魔力：').strip()
                    if mode_choice not in ('1', '2'):
                        print('判断属性模式不存在')
                        _reset_xd_state()
                        return False
                    xd_mode = 'base' if mode_choice == '1' else 'derived'
                if num == 2:
                    if xd_mode == 'base':
                        target_prompt = ('请输入成长+五项:(成长/体力/力量/耐力/敏捷/智力) '
                                         '成长/体力/力量/敏捷为不低于设定数值，'
                                         '耐力/智力为不高于设定数值，0为不判断')
                    else:
                        target_prompt = ('请输入成长+五项:(成长/生命值/攻击力/防御力/速度/魔力) '
                                         '成长/生命值/攻击力/速度为不低于设定数值，'
                                         '防御力/魔力为不高于设定数值，0为不判断')
                    target_length = 6
                else:
                    if xd_mode == 'base':
                        target_prompt = ('请输入五项:(体力/力量/耐力/敏捷/智力) '
                                         '体力/力量/敏捷为不低于设定数值，'
                                         '耐力/智力为不高于设定数值，0为不判断')
                    else:
                        target_prompt = ('请输入五项:(生命值/攻击力/防御力/速度/魔力) '
                                         '生命值/攻击力/速度为不低于设定数值，'
                                         '防御力/魔力为不高于设定数值，0为不判断')
                    target_length = 5
                target_values = input([target_prompt]).split()
                if len(target_values) != target_length:
                    print('请输入%d个用空格分隔的数值' % target_length)
                    _reset_xd_state()
                    return False
                try:
                    wx = [int(value) for value in target_values]
                except ValueError:
                    print('目标数值必须是整数')
                    _reset_xd_state()
                    return False
                xd_max_count = int(input('请输入洗点次数'))
                if xd_max_count <= 0:
                    print('洗点次数必须大于0')
                    _reset_xd_state()
                    return False
                eatwz(s, str2, pet, num, item_quantities)
                pending_result = True
                continue

            if _is_xd_target_reached(pet, wx, xd_mode):
                print('洗点成功，一共洗点%d次' % xd_count)
                action = _get_xd_continue_action()
                if action == '':
                    xd_count = 0
                    eatwz(s, str2, pet, num, item_quantities)
                    pending_result = True
                    continue
                if action == '886':
                    s, str2 = switch_server()
                    xd_count = 0
                    continue
                _reset_xd_state()
                return False
            if xd_count >= xd_max_count:
                print('已达到指定洗点次数，一共洗点%d次' % xd_count)
                action = _get_xd_continue_action()
                if action == '':
                    xd_count = 0
                    eatwz(s, str2, pet, num, item_quantities)
                    pending_result = True
                    continue
                if action == '886':
                    s, str2 = switch_server()
                    xd_count = 0
                    continue
                _reset_xd_state()
                return False

            print('不满足')
            eatwz(s, str2, pet, num, item_quantities)
            pending_result = True
            continue

        print('丸子编号不存在')
        _reset_xd_state()
        return False


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
    global xd_count
    item_id = _XD_ITEM_IDS[num]
    packet = [0, 0, 0, 26, 6, 34, *str2, 0, 0, 5, 172, 0, 0, 0, 0,
              *pet['pet_id_bytes'], *item_id.to_bytes(4, byteorder='big')]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)
    xd_count += 1
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


def _get_kd_continue_action(petid):
    return _get_continue_action(
        '[按回车使用相同设置继续开%s，输入886换服继续，按0退出]' %
        _get_item_name(petid))


def kd(s, str2, switch_server, uid, get_current_fwq):
    petid = int(input(['请输入开蛋的编号']))
    packet = [0, 0, 0, 22, 7, 208, *str2, 0, 0, 5, 179, 0, 0, 0, 0, *str2]
    t1 = tuple(packet)
    req = struct.pack(*('22B',), *t1)
    s.send(req)
    rec = s.recv(2048)
    r1 = tuple(rec)
    while r1[4] * 256 + r1[5] != 2000:
        s.send(req)
        rec = s.recv(2048)
        r1 = tuple(rec)
    exp = getexp(r1[(-8):(-4)])
    print('开蛋物品:%s,经验树剩余经验:%d' % (_get_item_name(petid), exp))
    expectwx = input(['请输入期望的数值(成长 生命 攻击 防御 速度 魔力),不追求的输入0，防御和魔力反向'])
    expectwx = expectwx.split(' ')
    expectwx = [int(x) for x in expectwx]
    kd_max_count = int(input(['请输入本轮最多开蛋数量，按0退出']))
    if kd_max_count == 0:
        return
    if kd_max_count < 0:
        print('开蛋数量必须大于0')
        return

    s1 = list()
    s1.append(int(petid / 65536))
    s1.append(int(petid / 256 % 256))
    s1.append(petid % 256)
    kd_count = 0
    kd_total_count = 0
    while True:
        batch_size = min(6, kd_max_count - kd_count)
        _fetch_item_from_store(s, str2, petid, batch_size)
        packet = [0, 0, 0, 22, 4, 106, *str2, 0, 0, 5, 77, 0, 0, 0, 0, 0, *s1]
        for i in range(batch_size):
            t1 = tuple(packet)
            req = struct.pack(*('22B',), *t1)
            s.send(req)
        kd_count += batch_size
        kd_total_count += batch_size

        time.sleep(1)

        pet_data = _get_pet_bag(s, str2)
        a = pet_data['pets']
        i = pet_data['pet_count']
        print('共有%d只宠物' % i)

        num = 0
        for x, pet in enumerate(a):
            _record_pet_result(pet, '开蛋', uid, get_current_fwq())
            print('您的第%d个宠物是：%s,成长值%s\n体力%s\t生命值%s\n力量%s\t攻击力%s\n耐力%s\t防御%s\n敏捷%s\t速度%s\n智力%s\t魔力%s' % (
                x + 1, pet['nick'], pet['grow_value'],pet['physique'], pet['hp_max'], pet['strength'], pet['attack'], pet['endurance'], pet['defense'],pet['quick'], pet['speed'], pet['intelligence'], pet['mp_max']))
            if _is_kd_target_reached(pet, expectwx):
                print('体力%s\t生命值%s\n力量%s\t攻击力%s\n耐力%s\t防御%s\n敏捷%s\t速度%s\n智力%s\t魔力%s' % (
                    pet['physique'], pet['hp_max'], pet['strength'], pet['attack'], pet['endurance'], pet['defense'],
                    pet['quick'], pet['speed'], pet['intelligence'], pet['mp_max']))
                print('\n\n')
                num += 1

        print('共有%d只符合要求的宠物,成长:%d,五项:(%d,%d,%d,%d,%d)' % (num, expectwx[0], expectwx[1], expectwx[2],
                                                                        expectwx[3], expectwx[4], expectwx[5]))

        packet = [0, 0, 0, 22, 7, 208, *str2, 0, 0, 5, 179, 0, 0, 0, 0, *str2]
        t1 = tuple(packet)
        req = struct.pack(*('22B',), *t1)
        s.send(req)
        rec = s.recv(2048)
        r1 = tuple(rec)
        while r1[4] * 256 + r1[5] != 2000:
            s.send(req)
            rec = s.recv(2048)
            r1 = tuple(rec)

        exp = getexp(r1[(-8):(-4)])
        reached_limit = kd_count >= kd_max_count
        print('本轮已开%d/%d个蛋，累计开了%d个蛋，经验树剩余经验%d' % (
            kd_count, kd_max_count, kd_total_count, exp))

        if num > 0:
            clean = int(input(['是否进行清理，按1清理，按0退出']))
        else:
            clean = 1
        if clean == 1:
            for pet in a:
                if pet['level'] > 1:
                    print('当前背包中有等级大于1的精灵，是否继续碰蛋')
                    if int(input(['按1继续，按0退出：'])) == 0:
                        return

            if exp < 3750:
                print('经验不足，是否继续。当前经验：%d' % exp)
                if int(input(['按1继续，按0退出：'])) == 0:
                    return
            for b, pet in enumerate(a):
                fpexp(625, pet, str2, s)
                _pet_back_home(s, str2, pet)
                if b % 2 == 1:
                    print('进行碰蛋')
                    pengdan(pet, a[b - 1], str2, s)
            clearbag(s, str2)
            if num > 0 or reached_limit:
                if reached_limit:
                    print('已达到本轮指定开蛋数量%d' % kd_max_count)
                action = _get_kd_continue_action(petid)
                if action == '0':
                    return
                if action == '886':
                    s, str2 = switch_server()
                kd_count = 0
        else:
            return


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


def _pet_back_home(s, str2, pet):
    """兼容旧调用：将宠物放回仓库。"""
    _pet_back_store(s, str2, pet)


def pengdan(pet1, pet2, str2, s):
    packet = [0, 0, 0, 0x1a, 0x06, 0x17, *str2, 0, 0, 5, 141, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)
    rec = s.recv(250)
    r = tuple(rec)
    while r.__len__() != 63:
        s.send(req)
        rec = s.recv(250)
        r = tuple(rec)

    len1 = r[20] * 256 + r[21]
    packet = [0, 0, 0, 26, 6, 23, *str2, 0, 0, 5, 141, 0, 0, 0, 0, 0, 0, int((len1 - 2) / 256), (len1 - 2) % 256, 0, 0,
              0, 2]
    t1 = tuple(packet)
    req = struct.pack(*('26B',), *t1)
    s.send(req)
    rec = s.recv(250)
    pet1_id = pet1['pet_id_bytes'] if isinstance(pet1, dict) else pet1[0:4]
    pet2_id = pet2['pet_id_bytes'] if isinstance(pet2, dict) else pet2[0:4]
    packet = [0, 0, 0, 30, 6, 79, *str2, 0, 0, 6, 235, 0, 0, 0, 0, *pet1_id, *pet2_id, 0, 0, 0, 0]
    t1 = tuple(packet)
    req = struct.pack(*('30B',), *t1)
    s.send(req)


class _BattlePacketReader:
    """读取战斗协议的网络帧负载（TMF 头固定 18 字节，大端序）。"""

    def __init__(self, packet):
        if len(packet) < 18:
            raise ValueError('战斗响应包头不完整')
        self.packet = packet
        self.offset = 18

    def read(self, fmt):
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.packet):
            raise ValueError('战斗响应包数据被截断（偏移%d）' % self.offset)
        value = struct.unpack_from(fmt, self.packet, self.offset)[0]
        self.offset += size
        return value

    def bytes(self, size):
        if size < 0 or self.offset + size > len(self.packet):
            raise ValueError('战斗响应包数据被截断（偏移%d）' % self.offset)
        value = self.packet[self.offset:self.offset + size]
        self.offset += size
        return value

    @property
    def remaining(self):
        return len(self.packet) - self.offset


def _battle_avatar_id(user_id, pet_id):
    return '%d_%d' % (user_id, pet_id)


def _parse_battle_action_return(packet):
    """解析 1310：fighterCount -> BattleActionInfo -> action loops。"""
    reader = _BattlePacketReader(packet)
    fighters = []
    fighter_count = reader.read('>I')
    if fighter_count > 64:
        raise ValueError('1310 fighterCount异常：%d' % fighter_count)
    for _ in range(fighter_count):
        action = {
            'seq': reader.read('>I'),
            'user_id': reader.read('>I'),
            'pet_id': reader.read('>I'),
            'state1': reader.read('>I'),
            'state2': reader.read('>I'),
            'talk_id': reader.read('>i'),
            'hp_change': reader.read('>i'),
            'mp_change': reader.read('>i'),
            'jisheng_user_id': reader.read('>I'),
            'jisheng_pet_id': reader.read('>I'),
            'jisheng_hp_change': reader.read('>h'),
        }
        action['avatar_id'] = _battle_avatar_id(action['user_id'], action['pet_id'])
        loop_count = reader.read('>H')
        if loop_count > 128:
            raise ValueError('1310 loopCount异常：%d' % loop_count)
        loops = []
        for index in range(loop_count):
            loop = {
                'index': index,
                'atk_user_id': reader.read('>I'),
                'atk_pet_id': reader.read('>I'),
                'rebound_state': reader.read('>B'),
                'atk_type': reader.read('>I'),
                'atk_level': reader.read('>B'),
                'increase_hp': reader.read('>h'),
                'rebound_hp': reader.read('>h'),
                'rebound_mp': reader.read('>h'),
                'use_item_id': reader.read('>I'),
                'apt_user_id': reader.read('>I'),
                'apt_pet_id': reader.read('>I'),
                'protector_pos': reader.read('>B'),
                'state1': reader.read('>I'),
                'state2': reader.read('>I'),
                'change_hp': reader.read('>h'),
                'change_mp': reader.read('>h'),
            }
            loop['atk_avatar_id'] = _battle_avatar_id(loop['atk_user_id'], loop['atk_pet_id'])
            loop['apt_avatar_id'] = _battle_avatar_id(loop['apt_user_id'], loop['apt_pet_id'])
            loops.append(loop)
        action['loops'] = loops
        fighters.append(action)
    if reader.remaining:
        raise ValueError('1310响应存在%d字节未解析数据' % reader.remaining)
    return fighters


def _parse_battle_sync_info(packet):
    """解析 1311：回合参战角色/宠物属性快照。"""
    reader = _BattlePacketReader(packet)

    def read_avatar():
        user_id = reader.read('>I')
        pet_id = reader.read('>I')
        avatar = {
            'user_id': user_id,
            'pet_id': pet_id,
            'pet_type_id': reader.read('>I'),
            'position': reader.read('>I'),
            'nick': reader.bytes(16).split(b'\x00', 1)[0].decode('utf-8', errors='replace'),
            'vip_level': reader.read('>I'),
            'color': reader.read('>I'),
            'level': reader.read('>I'),
            'hp': reader.read('>I'),
            'hp_max': reader.read('>I'),
            'mp': reader.read('>I'),
            'mp_max': reader.read('>I'),
            'health_level': reader.read('>I'),
            'change_body_id': reader.read('>I'),
            'auto_battle_count': reader.read('>I'),
        }
        invalid_count = reader.read('>I')
        if invalid_count > 128:
            raise ValueError('1311 invalidSkillCount异常：%d' % invalid_count)
        avatar['invalid_skill_ids'] = [reader.read('>I') for _ in range(invalid_count)]
        avatar['pet_state'] = reader.read('>B')
        avatar['race'] = reader.read('>B')
        avatar['can_catch'] = reader.read('>h') > 0
        avatar['elements'] = {
            'earth': reader.read('>B'),
            'water': reader.read('>B'),
            'fire': reader.read('>B'),
            'wind': reader.read('>B'),
        }
        avatar['profession'] = reader.read('>B')
        item_count = reader.read('>B')
        if item_count > 64:
            raise ValueError('1311 itemCount异常：%d' % item_count)
        avatar['clothes'] = [
            {'instance_id': reader.read('>I'), 'item_id': reader.read('>I'), 'level': reader.read('>H')}
            for _ in range(item_count)
        ]
        skill_count = reader.read('>B')
        if skill_count > 128:
            raise ValueError('1311 skillCount异常：%d' % skill_count)
        avatar['skills'] = [
            {
                'skill_id': reader.read('>I'),
                'max_level': reader.read('>B'),
                'current_level': reader.read('>B'),
                'disable_left_round': reader.read('>B'),
            }
            for _ in range(skill_count)
        ]
        avatar['avatar_id'] = _battle_avatar_id(user_id, pet_id)
        return avatar

    result = {
        'battle_time_stamp': reader.read('>I'),
        'battle_user_id': reader.read('>I'),
        'challenge_leader_id': reader.read('>I'),
        'sneak_flag': reader.read('>B'),
        'is_pk': reader.read('>B') == 1,
    }
    challenger_count = reader.read('>I')
    if challenger_count > 32:
        raise ValueError('1311 challengerCount异常：%d' % challenger_count)
    result['challengers'] = [read_avatar() for _ in range(challenger_count)]
    result['accept_leader_id'] = reader.read('>I')
    accepter_count = reader.read('>I')
    if accepter_count > 32:
        raise ValueError('1311 accepterCount异常：%d' % accepter_count)
    result['accepters'] = [read_avatar() for _ in range(accepter_count)]
    if reader.remaining:
        raise ValueError('1311响应存在%d字节未解析数据' % reader.remaining)
    return result


def _parse_battle_result_pve(packet):
    """解析 1318：经验变化、技能变化及具体掉落物品。"""
    reader = _BattlePacketReader(packet)
    result = {'avatars': [], 'skills': [], 'items': [], 'is_bag_full': False}
    avatar = {
        'pet_id': reader.read('>I'),
        'change_level': reader.read('>I'),
        # BattleResultAvatarInfo.changeExp 在客户端按有符号 int 读取，
        # 失败或扣减经验时不能截断为 0。
        'change_exp': reader.read('>i'),
        'protect_exp': reader.read('>I'),
        'remain_exp': reader.read('>H'),
    }
    result['avatars'].append(avatar)
    if avatar['pet_id'] == 0:
        skill_count = reader.read('>B')
        if skill_count > 128:
            raise ValueError('1318 skillCount异常：%d' % skill_count)
        result['skills'] = [
            {'skill_id': reader.read('>I'), 'change_level': reader.read('>B'), 'change_exp': reader.read('>I')}
            for _ in range(skill_count)
        ]
        result['is_bag_full'] = reader.read('>B') == 1
        item_count = reader.read('>B')
        if item_count > 128:
            raise ValueError('1318 itemCount异常：%d' % item_count)
        result['items'] = [
            {'item_id': reader.read('>I'), 'count': reader.read('>I')}
            for _ in range(item_count)
        ]
    # 部分服务器版本会在已知字段后追加协议扩展字节（长度并不固定），
    # 客户端当前也不会读取这些字段；保留原始尾部，避免把合法响应误判为损坏。
    result['reserved'] = reader.bytes(reader.remaining)
    return result


def _parse_battle_over_notice(packet):
    """解析 1319：战斗结果、任务统计和经验倍率状态。"""
    reader = _BattlePacketReader(packet)
    result = {
        'battle_type': reader.read('>I'),
        'result': reader.read('>I'),
        'flag': reader.read('>I'),
        'fly_to_map_id': reader.read('>I'),
    }
    task_count = reader.read('>I')
    if task_count > 128:
        raise ValueError('1319 taskCount异常：%d' % task_count)
    result['tasks'] = []
    for _ in range(task_count):
        result['tasks'].append({
            'task_id': reader.read('>I'),
            'node_id': reader.read('>I'),
            'monster_type_id': reader.read('>I'),
            'monster_count': reader.read('>H'),
            'monster_target_count': reader.read('>H'),
            'opponent_count': reader.read('>H'),
            'opponent_target_count': reader.read('>H'),
            'target_skill_used_times': reader.read('>H'),
        })
    result['exp_buff'] = {
        'exp_rate': reader.read('>I'),
        'remain_count': reader.read('>i'),
        'skill_exp_rate': reader.read('>I'),
        'skill_remain_count': reader.read('>i'),
        'pet_exp_rate': reader.read('>I'),
        'pet_remain_count': reader.read('>i'),
        'auto_battle_count': reader.read('>i'),
    }
    result['auto_hp'] = reader.read('>I')
    result['auto_mp'] = reader.read('>I')
    if reader.remaining:
        raise ValueError('1319响应存在%d字节未解析数据' % reader.remaining)
    return result


_AUTO_STORE_ITEM_IDS = frozenset({
    *range(180001, 180041),
    180007, 180057, 180058, 
    210003, 230003, 
    240001, 240002, 240003, 240004, 250007, 290011, 290012, 
    300050, 300051, 300052, 315005, 315007, 315015, 
    340001, 340002, 341007, 341009, 341011, 341012, 
    350050, 350051, 360037, 360039, 370057, 370107,
})


def _format_battle_rewards(rewards):
    """合并多个 1318 结算包中的掉落物品并生成可读文本。"""
    totals = {}
    for reward in rewards:
        for item in reward.get('items', ()):
            item_id = item['item_id']
            totals[item_id] = totals.get(item_id, 0) + item['count']
    if not totals:
        return '无道具掉落'
    return '，'.join('%sx%d' % (_get_item_name(item_id), totals[item_id])
                    for item_id in sorted(totals))


_BATTLE_START_TIMEOUT = 30
_BATTLE_RESULT_TIMEOUT = 10
_BATTLE_TRANSIENT_COMMANDS = frozenset((1012, 1306, 1308, 1313, 1316))


def _recv_packet_before_deadline(session, deadline, timeout_message):
    """在固定截止时间前读取一个完整帧。

    deadline 由外层等待过程创建，避免非目标包到达后重置总超时。
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(timeout_message)
    try:
        return session.recv_packet(timeout=remaining)
    except socket.timeout as exc:
        raise TimeoutError(timeout_message) from exc


def _receive_battle_until(session, commands, timeout, deferred_end_packets):
    """在总时限内等待指定的战斗命令。"""
    pending = set(commands)
    parsed = []
    battle_ended = False
    deadline = time.monotonic() + timeout
    command_text = '/'.join(str(command) for command in sorted(pending))
    timeout_message = '等待战斗响应%s超时（%g秒）' % (command_text, timeout)

    while pending:
        response = _recv_packet_before_deadline(
            session, deadline, timeout_message)
        command_id = int.from_bytes(response[4:6], byteorder='big')
        if command_id == 1310:
            parsed.append((command_id, _parse_battle_action_return(response)))
            pending.discard(command_id)
        elif command_id == 1311:
            parsed.append((command_id, _parse_battle_sync_info(response)))
            pending.discard(command_id)
        elif command_id == 1307:
            parsed.append((command_id, None))
            pending.discard(command_id)
        elif command_id == 1300:
            # BATTLE_INVITE 的 18 字节回包是错误确认，错误码位于
            # 包尾四字节。200076（0x00030d8c）表示本次移动点
            # 没有可遇到的黑化精灵。
            error_id = int.from_bytes(response[14:18], byteorder='big') \
                if len(response) >= 18 else None
            if len(response) == 18 and error_id:
                raise TimeoutError(
                    '21102 1300 未命中隐形怪（error=%d）' % error_id)
            parsed.append((command_id, None))
            pending.discard(command_id)
        elif command_id in pending:
            parsed.append((command_id, None))
            pending.discard(command_id)
        elif command_id == 1003:
            raise ConnectionError('服务器要求离开当前地图（1003）')
        elif command_id in (1318, 1319):
            deferred_end_packets.append(response)
            battle_ended = True
            pending.clear()
        elif command_id in _BATTLE_TRANSIENT_COMMANDS:
            # 资源加载、回合通知和角色标记等包只用于推进当前
            # 战斗，后续请求不会消费，不放入持久待处理队列。
            continue
        else:
            session._pending_by_command.setdefault(command_id, deque()).append(
                response)

    return parsed, battle_ended


def _build_hidden_map_enter_packet(str2, map_id=0x54f7,
                                   entry_x=0xac, entry_y=0xcf,
                                   sequence=None):
    """构造进入隐形怪地图的 1004 请求。

    map_id/entry_x/entry_y 是传送包中的地图和入口坐标；不同隐形怪地图
    只需要替换这三个配置，不需要复制遇怪逻辑。
    """
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


def _build_walk_packet(str2, start_x, start_y, end_x, end_y, direction,
                       walk_step=0, walk_type=0, sequence=None,
                       timestamp=None, channel=6):
    """构造客户端 WalkModule.sendWalk 使用的 1009 请求。"""
    if len(str2) != 4:
        raise ValueError('str2 必须是 4 字节用户标识')
    sequence = random.randint(0, 255) if sequence is None else sequence
    # 客户端写入的是 getTimer() * 0.001，即启动后的秒数，而不是 Unix 毫秒。
    timestamp = int(time.monotonic()) & 0xffffffff if timestamp is None else timestamp
    payload = struct.pack('>hhIIhIIhI', start_x, start_y, end_x, end_y,
                          walk_step, direction, walk_type, 0, timestamp)
    packet_length = 18 + len(payload)
    # SocketConnection 的 TMF 头：长度、命令、用户标识、会话/序号、保留字段。
    header = struct.pack('>IH4B4B4B', packet_length, 1009, *str2,
                         0, 0, channel, sequence, 0, 0, 0, 0)
    return header + payload


def _build_hidden_monster_invite(str2, sequence=None):
    """构造隐形怪遇敌请求：BATTLE_INVITE(1300, 0, 0, 0)。"""
    if len(str2) != 4:
        raise ValueError('str2 必须是 4 字节用户标识')
    sequence = random.randint(0, 255) if sequence is None else sequence
    # 1300 的参数是 3 个 uint（id、petGroupID、petIndexID）。
    payload = bytes(12)
    packet_length = 18 + len(payload)
    header = struct.pack('>IH4B4B4B', packet_length, 1300, *str2,
                         0, 0, 6, sequence, 0, 0, 0, 0)
    return header + payload


def _parse_player_walk_packet(packet, self_id):
    """解析 1009 回包；只返回自己的行走回包。"""
    if len(packet) < 18 + 22 or int.from_bytes(packet[4:6], 'big') != 1009:
        return None
    user_id, pos_x, pos_y, step, direction, walk_type = struct.unpack_from(
        '>IIIHII', packet, 18)
    if user_id != self_id:
        return None
    return {
        'pos_x': pos_x,
        'pos_y': pos_y,
        'step': step,
        'direction': direction,
        'type': walk_type,
    }


def _trace_hidden_event(enabled, label, packet=None, **fields):
    if not enabled:
        return
    details = ' '.join('%s=%s' % (key, value) for key, value in fields.items())
    if packet is not None:
        packet_hex = bytes(packet).hex(' ')
        details = '%s hex=%s' % (details, packet_hex) if details else 'hex=%s' % packet_hex
    print('[hidden-21102] %s%s' % (label, ' ' + details if details else ''))


def _parse_hidden_spawn_from_enter_map(packet):
    """从 1004 EnterMapProtocol 数据中提取本次出生坐标。"""
    # 该入口的 PersonInfo 中包含 mapID=0x54f7，后面固定跟随 7 个
    # 保留字节和两个坐标 uint。坐标会在两个出生点之间变化。
    marker = b'\x00\x00\x54\xf7'
    offset = packet.find(marker)
    if offset < 0 or offset + 16 > len(packet):
        return None
    start = offset + 8
    pos_x, pos_y = struct.unpack_from('>II', packet, start)
    if not (0 < pos_x < 10000 and 0 < pos_y < 10000):
        return None
    return pos_x, pos_y


def _parse_reset_walk_packet(packet):
    """解析 1034 RESET_WALK_INFO，用服务端位置恢复巡回状态。"""
    if len(packet) < 38 or int.from_bytes(packet[4:6], 'big') != 1034:
        return None
    step, start_x, start_y, pos_x, pos_y = struct.unpack_from(
        '>IIIII', packet, 18)
    if not (0 < pos_x < 10000 and 0 < pos_y < 10000):
        return None
    return {
        'step': step,
        'start_x': start_x,
        'start_y': start_y,
        'pos_x': pos_x,
        'pos_y': pos_y,
    }


_HIDDEN_WALK_INTERVAL = 0.5
_HIDDEN_BATTLE_ATTEMPTS = 10

# 每张隐形怪地图只配置两个实际行走点。第一点通常是传送后的出生点，
# 第二点是客户端持续移动到的固定点；后续自动在两点之间往返。
_HIDDEN_PATROL_ROUTES = {
    21102: {
        # 1004 传送包中的地图编号和入口坐标；不是角色当前坐标。
        'map_id': 0x54f7,
        'entry': (0xac, 0xcf),
        # 两个固定点之间的实际移动步数。21102 抓包中对应路线约为
        # 23--30；0 只适合首次贴近出生点，不适合战斗后的巡回。
        'walk_step': 30,
        'points': ((1179, 662), (1280, 900)),
    },
    21103: {
        # 1004 传送包中的地图编号和入口坐标；不是角色当前坐标。
        'map_id': 0x526f,
        'entry': (0x05a0, 0x0330),
        # 两个固定点之间的实际移动步数。21102 抓包中对应路线约为
        # 23--30；0 只适合首次贴近出生点，不适合战斗后的巡回。
        'walk_step': 30,
        'points': ((1440, 816), (1157, 672)),
    },
}


def _hidden_walk_direction(start, end):
    """按客户端方向编号推导两点之间的行走方向。"""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx >= 0 and dy < 0:
        return 1
    if dx >= 0:
        return 2
    if dy >= 0:
        return 5
    return 6


def _trigger_hidden_monster_patrol(s, str2, session, self_id,
                                   patrol_start, patrol_target,
                                   timeout, trace, max_attempts,
                                   start_pos=None, walk_step=30):
    """在两个固定点之间往返，并把剩余遇怪步数用于下一段路线。"""
    points = (
        tuple(int(value) for value in patrol_start),
        tuple(int(value) for value in patrol_target),
    )
    if any(len(point) != 2 for point in points) or points[0] == points[1]:
        raise ValueError('隐形怪巡回起点和目标点必须是两个不同坐标')
    current_pos = tuple(start_pos) if start_pos is not None else points[0]
    # 与稳定抓包路线一致：每次进入遇怪函数或 1300
    # 未命中后，先用 step=0 回到第一个巡回点校准状态。
    target_index = 0
    needs_alignment = True
    server_steps_remaining = None
    reset_retries = 0

    attempt = 0
    while max_attempts is None or attempt < max_attempts:
        attempt += 1
        pending_resets = session._pending_by_command.get(1034)
        if pending_resets:
            reset_packet = pending_resets.popleft()
            if not pending_resets:
                del session._pending_by_command[1034]
            reset_walk = _parse_reset_walk_packet(reset_packet)
            if reset_walk is None:
                raise ValueError('1034 RESET_WALK_INFO 响应格式无效')
            current_pos = (reset_walk['pos_x'], reset_walk['pos_y'])
            _trace_hidden_event(
                trace, 'consume pending 1034 RESET_WALK_INFO',
                reset_packet, attempt=attempt, step=reset_walk['step'],
                start=(reset_walk['start_x'], reset_walk['start_y']),
                pos=current_pos)
            needs_alignment = True
            server_steps_remaining = None
            target_index = 0
            reset_retries += 1
            if reset_retries >= 3:
                raise TimeoutError(
                    '隐形怪行走连续收到 1034，坐标校准失败')

        target = points[target_index]
        direction = _hidden_walk_direction(current_pos, target)

        # 1009 回包的 step 是下一段路线还能行走的遇怪步数。
        # 不能在当前段已到达 target 后，再从旧起点倒推停止坐标。
        if (not needs_alignment and server_steps_remaining is not None and
                0 < server_steps_remaining <= walk_step):
            distance = ((target[0] - current_pos[0]) ** 2 +
                        (target[1] - current_pos[1]) ** 2) ** 0.5
            ratio = min(1.0, server_steps_remaining * 5 / distance) \
                if distance else 0
            stop_pos = (
                round(current_pos[0] +
                      (target[0] - current_pos[0]) * ratio),
                round(current_pos[1] +
                      (target[1] - current_pos[1]) * ratio),
            )
            _trace_hidden_event(
                trace, 'next leg reached trigger threshold',
                attempt=attempt, remaining=server_steps_remaining,
                start=current_pos, target=target, stop_pos=stop_pos)
            stop_packet = _build_walk_packet(
                str2, stop_pos[0], stop_pos[1], stop_pos[0], stop_pos[1],
                direction=direction, walk_step=server_steps_remaining,
                walk_type=1, channel=6)
            s.send(stop_packet)
            current_pos = stop_pos
            _trace_hidden_event(
                trace, 'send 1009 STOP_CHECK', stop_packet,
                attempt=attempt, walk_step=server_steps_remaining)
            time.sleep(0.1)

            invite = _build_hidden_monster_invite(str2)
            s.send(invite)
            _trace_hidden_event(
                trace, 'send 1300 BATTLE_INVITE', invite,
                attempt=attempt, trigger='next-leg walkStepIdx>0')

            invite_deadline = time.monotonic() + timeout
            while time.monotonic() < invite_deadline:
                try:
                    response = session.recv_packet(
                        timeout=max(0.1, invite_deadline - time.monotonic()))
                except socket.timeout as exc:
                    raise TimeoutError('隐形怪等待 1300/1305 响应超时') from exc
                command_id = int.from_bytes(response[4:6], 'big')
                _trace_hidden_event(
                    trace, 'recv patrol battle command', response,
                    attempt=attempt, command=command_id)
                if command_id == 1305:
                    return {
                        'pos_x': current_pos[0],
                        'pos_y': current_pos[1],
                        'step': server_steps_remaining,
                        'direction': direction,
                        'type': 1,
                        'battle_started': True,
                    }
                if command_id == 1300 and len(response) == 18:
                    error_id = int.from_bytes(response[14:18], 'big')
                    _trace_hidden_event(
                        trace, 'recv 1300 ACK', response,
                        attempt=attempt, error=error_id)
                    if error_id in (200075, 200076):
                        _trace_hidden_event(
                            trace, '1300 miss; realign patrol',
                            attempt=attempt, pos=current_pos,
                            alignment_target=points[0])
                        needs_alignment = True
                        server_steps_remaining = None
                        target_index = 0
                        break
                    if error_id:
                        raise TimeoutError(
                            '隐形怪 1300 返回错误（error=%d）' % error_id)
                elif command_id == 1009:
                    continue
                else:
                    session._pending_by_command.setdefault(
                        command_id, deque()).append(response)
            else:
                raise TimeoutError('隐形怪等待 1300/1305 响应超时')
            time.sleep(_HIDDEN_WALK_INTERVAL)
            continue

        planned_step = 0 if needs_alignment else walk_step
        packet = _build_walk_packet(
            str2, current_pos[0], current_pos[1], target[0], target[1],
            direction=direction, walk_step=planned_step, walk_type=0, channel=6)
        s.send(packet)
        _trace_hidden_event(trace, 'send 1009 PATROL', packet,
                            attempt=attempt, start=current_pos, target=target)

        deadline = time.monotonic() + timeout
        walk = None
        reset_walk = None
        while time.monotonic() < deadline:
            try:
                response = session.recv_packet(
                    timeout=max(0.1, deadline - time.monotonic()))
            except socket.timeout as exc:
                raise TimeoutError('隐形怪巡回移动等待 1009 超时') from exc
            command_id = int.from_bytes(response[4:6], 'big')
            if command_id == 1034:
                reset_walk = _parse_reset_walk_packet(response)
                if reset_walk is None:
                    raise ValueError('1034 RESET_WALK_INFO 响应格式无效')
                break
            if command_id != 1009:
                _trace_hidden_event(trace, 'recv patrol command', response,
                                    attempt=attempt, command=command_id)
                session._pending_by_command.setdefault(command_id, deque()).append(response)
                continue
            walk = _parse_player_walk_packet(response, self_id)
            if walk is not None:
                break
        if reset_walk is not None:
            current_pos = (reset_walk['pos_x'], reset_walk['pos_y'])
            _trace_hidden_event(
                trace, 'recv 1034 RESET_WALK_INFO; retry patrol',
                response, attempt=attempt, step=reset_walk['step'],
                start=(reset_walk['start_x'], reset_walk['start_y']),
                pos=current_pos, alignment_target=points[0])
            needs_alignment = True
            server_steps_remaining = None
            target_index = 0
            reset_retries += 1
            if reset_retries >= 3:
                raise TimeoutError(
                    '隐形怪行走连续收到 1034，坐标校准失败')
            time.sleep(0.2)
            continue
        if walk is None:
            raise TimeoutError('隐形怪巡回移动等待 1009 超时')

        current_pos = (walk['pos_x'], walk['pos_y'])
        server_steps_remaining = walk['step']
        reset_retries = 0
        _trace_hidden_event(trace, 'recv 1009 SELF', response,
                            attempt=attempt, server_remaining=walk['step'],
                            pos=current_pos, response_type=walk['type'],
                            direction=walk['direction'])
        needs_alignment = False
        if current_pos == target:
            target_index = 1 - target_index
        time.sleep(_HIDDEN_WALK_INTERVAL)

    raise TimeoutError('隐形怪巡回%d轮仍未遇到隐形怪' % max_attempts)


def _trigger_hidden_monster(s, str2, patrol_start, patrol_target,
                            timeout=10, trace=True,
                            max_attempts=_HIDDEN_BATTLE_ATTEMPTS,
                            start_pos=None, patrol_walk_step=30):
    """在传入的起点和目标点之间巡回，直到隐形怪战斗开始。"""
    session = _get_socket_session(s)
    self_id = int.from_bytes(bytes(str2), 'big')
    return _trigger_hidden_monster_patrol(
        s, str2, session, self_id, patrol_start, patrol_target,
        timeout, trace, max_attempts, start_pos=start_pos,
        walk_step=patrol_walk_step)


_BATTLE_WAIT_DEFAULTS = {
    1: (0.05, 0.5),
    2: (0.1, 4),
    3: (0.05, 1),
    4: (0.2, 2),
    5: (0.2, 2),
    6: (0.05, 0.5),
    7: (0.05, 0.5),
    8: (0.1, 2),
}

_HOURLY_RELOGIN_LEAD_SECONDS = 10
_HOURLY_RELOGIN_RESUME_SECONDS = 10


class _HourlyReloginRequired(Exception):
    """战斗进行中进入整点窗口，要求主动断开并重登。"""


def _next_hour_timestamp(now=None):
    """返回本地时区下一个整点的 Unix 时间戳。"""
    if now is None:
        now = time.time()
    local_now = time.localtime(now)
    return time.mktime((
        local_now.tm_year,
        local_now.tm_mon,
        local_now.tm_mday,
        local_now.tm_hour + 1,
        0,
        0,
        -1,
        -1,
        -1,
    ))


def _seconds_until_next_hour(now=None):
    current = time.time() if now is None else now
    return max(0, _next_hour_timestamp(current) - current)


def _hourly_relogin_due(now=None):
    """判断是否已进入整点前 10 秒的主动重登窗口。"""
    return _seconds_until_next_hour(now) <= _HOURLY_RELOGIN_LEAD_SECONDS


def _close_socket_quietly(sock):
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


def _relogin_at_hour_boundary(s, login_socket, uid, pwd, fwq=None):
    """关闭当前会话，等待整点后 10 秒，再重新登录。"""
    next_hour = _next_hour_timestamp()
    _close_socket_quietly(s)
    _close_socket_quietly(login_socket)

    resume_at = next_hour + _HOURLY_RELOGIN_RESUME_SECONDS
    wait_seconds = max(0, resume_at - time.time())
    print('距离整点不足%d秒，已主动下线，整点后%d秒重新登录' % (
        _HOURLY_RELOGIN_LEAD_SECONDS,
        _HOURLY_RELOGIN_RESUME_SECONDS,
    ))
    if wait_seconds:
        time.sleep(wait_seconds)

    login_result = login_taomi(
        uid,
        pwd,
        model=1,
        fwq=fwq if fwq in range(1, 21) else 0,
    )
    if login_result is None:
        raise ConnectionError('整点重登失败')
    print('整点后%d秒重新登录成功，继续自动战斗' %
          _HOURLY_RELOGIN_RESUME_SECONDS)
    return login_result


def _sleep_battle_cooldown(seconds):
    """等待战斗冷却，同时允许在整点窗口到达时及时返回。"""
    remaining = max(0, seconds)
    while remaining > 0:
        if _hourly_relogin_due():
            return True
        sleep_seconds = min(remaining, 1)
        time.sleep(sleep_seconds)
        remaining -= sleep_seconds
    return _hourly_relogin_due()


def get_battle_wait_defaults(position):
    """返回指定地点原有的回合和战斗结束等待时间。"""
    try:
        return _BATTLE_WAIT_DEFAULTS[position]
    except KeyError as exc:
        raise ValueError('不支持的战斗地点: %s' % position) from exc


def _input_wait(prompt, default):
    value = input('%s（默认 %s 秒，直接回车保持默认）: ' % (prompt, default)).strip()
    if not value:
        return default

    wait = float(value)
    if wait < 0:
        raise ValueError('%s不能为负数' % prompt)
    return wait


def prompt_battle_waits(position):
    """允许用户覆盖指定地点的默认战斗等待时间。"""
    default_turn_wait, default_end_wait = get_battle_wait_defaults(position)
    turn_wait = _input_wait('请输入每回合等待时间', default_turn_wait)
    end_wait = _input_wait('请输入战斗结束等待时间', default_end_wait)
    return turn_wait, end_wait


def battle(s, str2, position, turn_wait, end_wait, login_socket=None, reconnect_uid=None,
           reconnect_pwd=None, reconnect_fwq=None):
    """执行自动战斗；断线重连时使用传入的账号凭据。"""
    global mmh, mmh_mm
    if reconnect_uid is None:
        reconnect_uid = mmh
    if reconnect_pwd is None:
        reconnect_pwd = mmh_mm
    battle_times = 0
    reconnect_attempts = 0

    if _hourly_relogin_due():
        login_socket, s, str2 = _relogin_at_hour_boundary(
            s,
            login_socket,
            reconnect_uid,
            reconnect_pwd,
            reconnect_fwq,
        )

    def sleep_for_battle(seconds):
        """战斗过程分段等待，进入整点窗口时立即中断。"""
        remaining = max(0, seconds)
        while remaining > 0:
            if _hourly_relogin_due():
                raise _HourlyReloginRequired
            sleep_seconds = min(remaining, 1)
            time.sleep(sleep_seconds)
            remaining -= sleep_seconds

    pet_data = _get_pet_bag(s, str2)
    a = pet_data['pets']
    i = pet_data['pet_count']
    pet_flag = False
    for x in range(i):
        if a[x]['status'] == '主战':
            pet_id = a[x]['pet_id_bytes']
            pet_flag = True
            print(f"找到主战宠物{a[x]['nick']}")
        else:
            pass
    if not pet_flag:
        pet_id = [0, 0, 0, 0]
        print("没有主战宠物！")

    time.sleep(0.1)

    battle_load_wait = 0.05
    if position == 1:
        # 海滩
        packet = [0, 0, 0, *[0x26, 0x03, 0xec], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0,
                    *[0x56, 0x55], 0, 0, 0, 0, 0, 0, *[0x0, 0x5a], 0, 0, *[0x01, 0x8e], 0, 0, 0, 0]
        req = struct.pack(*('38B',), *packet)
        s.send(req)
        time.sleep(0.3)

    elif position == 2:
        # 草木树海
        packet = [0, 0, 0, *[0x26, 0x03, 0xec], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0,
                    *[0x2c, 0xef], 0, 0, 0, 0, 0, 0, *[0x05, 0x66], 0, 0, *[0x03, 0xee], 0, 0, 0, 0]
        req = struct.pack(*('38B',), *packet)
        s.send(req)
        time.sleep(0.3)

    elif position == 3:
        # 吉普豆 3 号地道
        hidden_map_id=21102
        hidden_route = _HIDDEN_PATROL_ROUTES.get(hidden_map_id)
        if hidden_route is None:
            raise ValueError('未配置隐形怪地图%d的传送和巡回点' % hidden_map_id)

        entry_x, entry_y = hidden_route['entry']
        req = _build_hidden_map_enter_packet(
            str2, hidden_route['map_id'], entry_x, entry_y)
        s.send(req)
        patrol_start, patrol_target = hidden_route['points']

        time.sleep(0.3)

    elif position == 4:
        # 传送新生巨石蟹
        packet = [0, 0, 0, 0x26, 3, 0xec, *str2, 0, 0, random.randint(5, 6),
            random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0x75, 0xfb, 0,
            0, 0, 0, 0, 0, 0, 0x8b, 0, 0, 0x01, 0x5d, 0, 0, 0, 0]
        s.send(struct.pack('38B', *packet))
        time.sleep(0.3)
    
    elif position == 5:
        # 传送yy
        packet = [0, 0, 0, *[0x26, 0x03, 0xec], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0,
                    *[0x54, 0xC8], 0, 0, 0, 0, 0, 0, *[0x03, 0x20], 0, 0, *[0x03, 0x3e], 0, 0, 0, 0]
        req = struct.pack(*('38B',), *packet)
        s.send(req)
        time.sleep(0.3)
    
    elif position == 6:
        # 克拉斯岩洞外
        packet = [0, 0, 0, *[0x26, 0x03, 0xec], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0,
                    *[0x56, 0x56], 0, 0, 0, 0, 0, 0, *[0x01, 0xae], 0, 0, *[0x00, 0xdc], 0, 0, 0, 0]
        req = struct.pack(*('38B',), *packet)
        s.send(req)
        time.sleep(0.3)

    elif position == 7:
        # 平原1
        packet = [0, 0, 0, *[0x26, 0x03, 0xec], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0,
                    *[0x53, 0x39], 0, 0, 0, 0, 0, 0, *[0x05, 0xfb], 0, 0, *[0x01, 0xdd], 0, 0, 0, 0]
        req = struct.pack(*('38B',), *packet)
        s.send(req)
        time.sleep(0.3)

    elif position == 8:
        # 吉普豆 2 号地道
        hidden_map_id=21103
        hidden_route = _HIDDEN_PATROL_ROUTES.get(hidden_map_id)
        if hidden_route is None:
            raise ValueError('未配置隐形怪地图%d的传送和巡回点' % hidden_map_id)

        entry_x, entry_y = hidden_route['entry']
        req = _build_hidden_map_enter_packet(
            str2, hidden_route['map_id'], entry_x, entry_y)
        s.send(req)
        patrol_start, patrol_target = hidden_route['points']

        time.sleep(0.3)

    hidden_position = None
    while True:
        try:
            if _hourly_relogin_due():
                login_socket, s, str2 = _relogin_at_hour_boundary(
                    s,
                    login_socket,
                    reconnect_uid,
                    reconnect_pwd,
                    reconnect_fwq,
                )
                hidden_position = None

            # 星豆治疗
            packet = [0, 0, 0, 22, 4, 1, *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 5]
            req = struct.pack(*('22B',), *packet)
            s.send(req)
            sleep_for_battle(0.1)
            if position == 1:
                # 刷明雷
                packet = [0, 0, 0, 0x1e, 0x05, 0x14, *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 9, 0,
                          0, 0, 0, 0, 0, 0, 0]
                req = struct.pack(*('30B',), *packet)
                s.send(req)
                sleep_for_battle(0.1)

            elif position == 2:
                # 刷食人花
                packet = [0, 0, 0, *[0x1a, 0x05, 0x18], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0,
                          0x1e, 0, 0, 0, 0]
                # # 高级骷髅王
                # packet = [0, 0, 0, *[0x1a, 0x05, 0x18], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0x0b,
                #           0x5e, 0, 0, 0, 0]
                req = struct.pack(*('26B',), *packet)
                s.send(req)
                sleep_for_battle(0.1)

            elif (position == 3) or (position == 8):
                hidden_result = _trigger_hidden_monster(
                    s,
                    str2,
                    patrol_start,
                    patrol_target,
                    trace=False,
                    max_attempts=None,
                    start_pos=hidden_position,
                    patrol_walk_step=hidden_route['walk_step'],
                )
                hidden_position = (
                    hidden_result.get('pos_x'), hidden_result.get('pos_y'))
                sleep_for_battle(0.1)
            elif position == 4:
                # 2. 刷明雷战斗
                packet = [0, 0, 0, 0x1a, 0x05, 0x18, *str2, 0, 0, random.randint(5, 6),
                        random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0x09, 0xc7, 0, 0, 0, 0]
                s.send(struct.pack('26B', *packet))
                sleep_for_battle(0.1)

            elif position == 5:
                # 2. 刷明雷战斗
                packet = [0, 0, 0, 0x1a, 0x05, 0x18, *str2, 0, 0, random.randint(5, 6),
                        random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0x07, 0xda, 0, 0, 0, 0]
                s.send(struct.pack('26B', *packet))
                sleep_for_battle(0.1)
            
            elif position == 6:
                # 2. 刷明雷战斗
                packet = [0, 0, 0, 0x1e, 0x05, 0x14, *str2, 0, 0, random.randint(5, 6),
                        random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0x00, 0x0e, 0, 0, 0, 0, 0, 0, 0, 0]
                s.send(struct.pack('30B', *packet))
                sleep_for_battle(0.1)

            elif position == 7:
                # 2. 刷明雷战斗
                packet = [0, 0, 0, 0x1e, 0x05, 0x14, *str2, 0, 0, random.randint(5, 6),
                        random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0x00, 0x0b, 0, 0, 0, 0, 0, 0, 0, 0]
                s.send(struct.pack('30B', *packet))
                sleep_for_battle(0.1)

            deferred_end_packets = []
            battle_ended = [False]

            def send_packet(packet):
                s.send(struct.pack('%dB' % len(packet), *packet))

            def receive_until(commands, timeout=10):
                """读取并解析服务器帧，直到收到指定命令集合。"""
                if _hourly_relogin_due():
                    raise _HourlyReloginRequired
                until_window = (
                    _seconds_until_next_hour() - _HOURLY_RELOGIN_LEAD_SECONDS)
                receive_timeout = min(timeout, until_window)
                if receive_timeout <= 0:
                    raise _HourlyReloginRequired
                limited_by_hourly_window = receive_timeout < timeout
                try:
                    parsed, ended = _receive_battle_until(
                        _get_socket_session(s), commands, receive_timeout,
                        deferred_end_packets)
                except TimeoutError:
                    if limited_by_hourly_window or _hourly_relogin_due():
                        raise _HourlyReloginRequired
                    raise
                if ended:
                    battle_ended[0] = True
                return parsed

            # 1300 只是遇敌请求确认；客户端还会等待 1305
            # BATTLE_START，收到后才开始发送 1306 资源加载进度。
            if position == 3 and not hidden_result.get('battle_started'):
                receive_until({1305}, timeout=10)

            for battle_load_percent in [1, 38, 76, 100]:
                # BATTLE_RES_LOAD_PROGRESS 1306：进入战斗读秒（0-100）。
                send_packet([0, 0, 0, 22, 5, 26, *str2, 0, 0, random.randint(5, 6),
                             random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, battle_load_percent])
                # 新生巨石蟹的触发/读秒阶段处理较慢，过快连续发送
                # 1306 会被服务器按异常会话复位连接。
                sleep_for_battle(battle_load_wait)

            # BATTLE_INIT_STATE 1317，然后等待 BATTLE_BEGIN_NOTICE 1307。
            send_packet([0, 0, 0, 22, 5, 37, *str2, 0, 0, 5, random.randint(0, 255),
                         0, 0, 0, 0, 0, 0, 0, 1])
            # 新生巨石蟹地图的 1304/1305 触发流程较慢，服务端可能在
            # 1317 确认后持续发送 1306/1316 十几秒才发 1307。
            receive_until({1307}, timeout=_BATTLE_START_TIMEOUT)

            # 持续提交回合行动，直到服务器发来 1318 结算包。
            # 战斗可能在任意回合结束，不能按地点预设回合数截断。
            round_count = 0
            while not battle_ended[0]:
                round_count += 1
                send_packet([0, 0, 0, 38, 5, 28, *str2, 0, 0, 5, random.randint(0, 255),
                             0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                             255, 255, 255, 255, 0, 15, 66, 64, 0, 0, 0, 1])
                sleep_for_battle(turn_wait)
                send_packet([0, 0, 0, 38, 5, 28, *str2, 0, 0, 5, random.randint(0, 255),
                             0, 0, 0, 0, *pet_id, 0, 0, 0, 0, 255, 255, 255, 255,
                             0, 15, 66, 64, 0, 0, 0, 1])
                sleep_for_battle(turn_wait)
                # SET_ROLE_FLAG 1012：通知服务器本回合行动已提交。
                send_packet([0, 0, 0, 26, 3, 244, *str2, 0, 0, 5, random.randint(0, 255),
                             0, 0, 0, 0, 0, 0, 0, 0x20, 0, 0, 0, 0])
                receive_until({1310, 1311})
                if battle_ended[0]:
                    break
                # 下一回合不要紧贴上一回合响应发送，给服务端完成回合
                # 状态切换的时间；战斗过程记录中动作间隔约为 50ms。
                sleep_for_battle(turn_wait)

            # 1318 可能按掉落条目重复返回；1319 才是战斗结束通知。
            rewards = []
            result_deadline = time.monotonic() + _BATTLE_RESULT_TIMEOUT
            while True:
                if deferred_end_packets:
                    response = deferred_end_packets.pop(0)
                else:
                    response = _recv_packet_before_deadline(
                        _get_socket_session(s), result_deadline,
                        '等待战斗结算 1319 超时（%d秒）' %
                        _BATTLE_RESULT_TIMEOUT)
                command_id = int.from_bytes(response[4:6], byteorder='big')
                if command_id == 1318:
                    rewards.append(_parse_battle_result_pve(response))
                elif command_id == 1319:
                    over = _parse_battle_over_notice(response)
                    battle_result = over['result']
                    reward_text = _format_battle_rewards(rewards)
                    break
                elif command_id == 1310:
                    _parse_battle_action_return(response)
                elif command_id == 1311:
                    _parse_battle_sync_info(response)
                elif command_id in _BATTLE_TRANSIENT_COMMANDS:
                    continue
                else:
                    _get_socket_session(s)._pending_by_command.setdefault(command_id, deque()).append(response)

            # # 提交战斗任务进度（TASK_SUBMIT_BUFFER 1165），并确认服务器已接收。
            # task_packet = [0, 0, 0, 154, 4, 141, *str2, 0, 0, 6,
            #                random.randint(0, 255)] + [0] * 140
            # task_packet[20:24] = [0x98, 0x5B, 0x03, 0x0B]
            # task_packet[71:75] = [0x0C, 0xFA, 0x00, 0x01]
            # send_packet(task_packet)
            # receive_until({1165})

            # 战斗退出及角色/背包刷新请求。对应响应暂按需求忽略。
            send_packet([0, 0, 0, 26, 4, 6, *str2, 0, 0, 5,
                         random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1])
            send_packet([0, 0, 0, 18, 5, 64, *str2, 0, 0, 5,
                         random.randint(0, 255), 0, 0, 0, 0])
            # 等待 BATTLE_MODULE_EXIT_REPORT (1344) 确认，避免下一场
            # 新生巨石蟹触发包紧贴上一场退出请求发送。
            receive_until({1344}, timeout=10)
            # send_packet([0, 0, 0, 18, 6, 18, *str2, 0, 0, 5,
            #              random.randint(0, 255), 0, 0, 0, 0])
            # send_packet([0, 0, 0, 18, 4, 82, *str2, 0, 0, 5,
            #              random.randint(0, 255), 0, 0, 0, 0])
            # send_packet([0, 0, 0, 22, 3, 238, *str2, 0, 0, 5,
            #              random.randint(0, 255), 0, 0, 0, 0, *str2])

            if battle_result == 1:
                battle_times += 1
                print('%s:战斗结束，累计完成%d次战斗，共%d回合，获得战利品%s' %
                      (time.strftime('%H:%M:%S'), battle_times, round_count, reward_text))
            elif battle_result == 2:
                print('%s:战斗失败，共%d回合' % (time.strftime('%H:%M:%S'), round_count))
            else:
                print('%s:战斗结果%d' % (time.strftime('%H:%M:%S'), battle_result))
            reconnect_attempts = 0

            _repair_equipment(s, str2)

            # 连续无间隔刷战斗会触发服务端连接保护；每场结束后留出
            # 一段冷却时间，避免下一场请求紧贴结算/刷新包。
            if battle_times % 50 == 0:
                prop_info, _ = _get_prop_bag_info(s, str2)
                for prop in prop_info:
                    inid = prop['item_id']
                    if inid in [341007, 341009, 341012]:
                        _prop_sell(s, str2, inid, prop['quantity'])
                        print('%s已自动出售,共%d个' % (_get_item_name(inid), prop['quantity']))
                    if inid in _AUTO_STORE_ITEM_IDS:
                        _prop_backto_store(s, str2, inid, prop['quantity'])
                        print('%s已放入仓库,共%d个' % (_get_item_name(inid), prop['quantity']))
                equipment_info, _ = _get_equipment_bag_info(s, str2)
                for equipment in equipment_info:
                    inid = equipment['item_id']
                    if inid in [
                        142501, 142502, 142503, 142504, 142505, # 紫炼套装
                        142001, 142002, 142003, 142004, 142005, # 祈福套装
                        140280, 140281, 140282                  # 狩猎套装
                        ] :
                        _equipment_sell(s, str2, inid, equipment['instance_id'])
                    if (inid >= 80070 and inid <= 80077): # 绅士套装/淑女套装
                        _equipment_discard(s, str2, inid, equipment['instance_id'])
            if _sleep_battle_cooldown(end_wait):
                login_socket, s, str2 = _relogin_at_hour_boundary(
                    s,
                    login_socket,
                    reconnect_uid,
                    reconnect_pwd,
                    reconnect_fwq,
                )
                hidden_position = None
                reconnect_attempts = 0
        except _HourlyReloginRequired:
            login_socket, s, str2 = _relogin_at_hour_boundary(
                s,
                login_socket,
                reconnect_uid,
                reconnect_pwd,
                reconnect_fwq,
            )
            hidden_position = None
            reconnect_attempts = 0
        # SocketSession 在对端主动关闭时抛出 ConnectionError；与连接重置/
        # 中止一样重新登录，避免战斗循环因未捕获异常直接退出。
        except (ConnectionError, TimeoutError) as exc:
            reconnect_attempts += 1
            delay = min(60, 5 * (2 ** min(reconnect_attempts - 1, 3)))
            print('战斗连接异常：%s，%d秒后重新登录（第%d次）' %
                  (exc, delay, reconnect_attempts))
            for stale_socket in (s, login_socket):
                _close_socket_quietly(stale_socket)
            time.sleep(delay)
            login_result = login_taomi(
                reconnect_uid,
                reconnect_pwd,
                model=1,
                fwq=reconnect_fwq if (reconnect_fwq in range(1, 11)) else 0,
            )
            if login_result is None:
                raise ConnectionError('战斗重连登录失败') from exc
            login_socket, s, str2 = login_result
            hidden_map_entered = False
            hidden_position = None

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


def shared_socket_thread_test(s, str2):
    """启动两个线程，并让它们共享同一个 socket。"""

    def first_thread(s, str2):
        global mmh, mmh_mm
        battle_times = 0

        pet_data = _get_pet_bag(s, str2)
        a = pet_data['pets']
        i = pet_data['pet_count']
        pet_flag = False
        for x in range(i):
            if a[x]['status'] == '主战':
                pet_id = a[x]['pet_id_bytes']
                pet_flag = True
                print(f"找到主战宠物{a[x]['nick']}")
            else:
                pass
        if not pet_flag:
            pet_id = [0, 0, 0, 0]
            print("没有主战宠物！")

        time.sleep(0.1)

        while True:
            try:
                skill_time = 8
                # 传送海滩
                packet = [0, 0, 0, *[0x26, 0x03, 0xec], *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0,
                        *[0x56, 0x55], 0, 0, 0, 0, 0, 0, *[0x0, 0x5a], 0, 0, *[0x01, 0x8e], 0, 0, 0, 0]
                req = struct.pack(*('38B',), *packet)
                s.send(req)
                time.sleep(0.1)

                # 刷明雷
                packet = [0, 0, 0, 30, 5, 20, *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 9, 0,
                        0, 0, 0, 0, 0, 0, 0]
                req = struct.pack(*('30B',), *packet)
                s.send(req)
                time.sleep(0.1)

                for battle_load_percent in range(5, 101, 5):
                    # 进入战斗读秒（0-100）
                    packet = [0, 0, 0, 22, 5, 26, *str2, 0, 0, random.randint(5, 6), random.randint(0, 255), 0, 0, 0, 0, 0,
                            0, 0, battle_load_percent]
                    req = struct.pack(*('22B',), *packet)
                    s.send(req)
                    time.sleep(0.1)

                # 不知道干啥用的，大概是进入战斗
                packet = [0, 0, 0, 22, 5, 37, *str2, 0, 0, 6, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 1]
                req = struct.pack(*('22B',), *packet)
                s.send(req)
                time.sleep(0.1)

                # packet = [0, 0, 0, 18, 6, 28, *str2, 0, 0, 5, 221, 0, 0, 0, 0, 0, 0, 0, 0]
                # t1 = tuple(packet)
                # req = (struct.pack)(*('22B', ), *t1)
                # s.send(req)
                # time.sleep(0.2)

                # 自动释放技能
                for i in range(0, skill_time):
                    # 人物自动攻击
                    packet = [0, 0, 0, 38, 5, 28, *str2, 0, 0, 6, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                            0,
                            255, 255, 255, 255, 0, 15, 66, 64, 0, 0, 0, 1]
                    req = struct.pack(*('38B',), *packet)
                    s.send(req)
                    time.sleep(0.05)

                    # 宠物自动攻击
                    packet = [0, 0, 0, 38, 5, 28, *str2, 0, 0, 6, random.randint(0, 255), 0, 0, 0, 0, *pet_id, 0, 0,
                            0, 0, 255, 255, 255, 255, 0, 15, 66, 64, 0, 0, 0, 1]
                    req = struct.pack(*('38B',), *packet)
                    s.send(req)
                    time.sleep(0.05)
                    # SET_ROLE_FLAG 1012：通知服务器本回合行动已提交。
                    packet = [0, 0, 0, 26, 3, 244, *str2, 0, 0, 5, random.randint(0, 255),
                             0, 0, 0, 0, 0, 0, 0, 0x20, 0, 0, 0, 0]
                    req = struct.pack(*('26B',), *packet)
                    s.send(req)
                    time.sleep(0.05)         

                    #原版有'h12064a头的包，不知道干什么用的
                    packet = [0, 0, 0, 18, 6, 74, *str2, 0, 0, 5, random.randint(0,255), 0, 0, 0, 0]
                    t1 = tuple(packet)
                    req = (struct.pack)(*('18B', ), *t1)
                    s.send(req)
                    time.sleep(0.2)

                task_packet = [0, 0, 0, 154, 4, 141, *str2, 0, 0, 6,
                        random.randint(0, 255)] + [0] * 140
                task_packet[20:24] = [0x98, 0x5B, 0x03, 0x0B]
                task_packet[71:75] = [0x0C, 0xFA, 0x00, 0x01]
                t1 = tuple(task_packet)
                req = (struct.pack)(*('154B', ), *t1)
                s.send(req)

                # 大概是结束战斗（1a0406）
                packet = [0, 0, 0, 26, 4, 6, *str2, 0, 0, 6, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1]
                t1 = tuple(packet)
                req = struct.pack(*('26B',), *t1)
                s.send(req)
                time.sleep(0.1)

                packet = [0, 0, 0, 18, 5, 64, *str2, 0, 0, 5,
                         random.randint(0, 255), 0, 0, 0, 0]
                t1 = tuple(packet)
                req = struct.pack(*('18B',), *t1)
                s.send(req)
                time.sleep(0.1)

                # 星豆治疗
                packet = [0, 0, 0, 22, 4, 1, *str2, 0, 0, 5, random.randint(0, 255), 0, 0, 0, 0, 0, 0, 0, 5]
                req = struct.pack(*('22B',), *packet)
                s.send(req)

                battle_times = battle_times + 1
                print(time.strftime('%H:%M:%S ') + f"完成第{battle_times}次战斗")

                time.sleep(0.1)
            except (ConnectionAbortedError, ConnectionResetError):
                _, s, str2 = login_taomi(mmh, mmh_mm, model=1, fwq=0)

    def second_thread(s, str2):
        global mmh, mmh_mm

        while True:
            try:
                if random.randint(1, 11) == 1:
                    # 洗点：1/11 概率。
                    packet = [00,00,00,0x1A,0x06,0x1D,*str2,00,00,0x05,0x06,00,00,00,00,0x63,0xFB,0x80,0x2A,0x00,0x05,0x7E,0x4A]
                    t1 = tuple(packet)
                    req = struct.pack(*('26B',), *t1)
                    s.send(req)
                    print('洗点')
                else:
                    # 加点：10/11 概率，是洗点概率的 10 倍。
                    packet = [00,00,00,0x20,0x06,0x46,*str2,00,00,0x05,0x07,00,00,00,00,0x63,0xFB,0x80,0x2A,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x00,0x00,0x00]
                    t1 = tuple(packet)
                    req = struct.pack(*('32B',), *t1)
                    s.send(req)
                    print('加点')

                time.sleep(0.05)
            except (ConnectionAbortedError, ConnectionResetError):
                _, s, str2 = login_taomi(mmh, mmh_mm, model=1, fwq=0)

    threads = [
        threading.Thread(target=first_thread, args=(s,str2), name='shared-socket-thread-1'),
        threading.Thread(target=second_thread, args=(s,str2), name='shared-socket-thread-2'),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

def wpe_test(s, str2):
    """通过 1125 协议获取指定数量的指定物品。"""
    try:
        item_code = int(input('请输入物品代码（十进制，如 300080）：').strip())
        quantity = int(input('请输入物品数量（十进制）：').strip())
    except ValueError:
        print('物品代码和数量必须是整数')
        return

    if not 0 < item_code <= 0xFFFFFFFF:
        print('物品代码必须在 1 到 4294967295 范围内')
        return
    if not 0 < quantity <= 0xFFFFFFFF:
        print('物品数量必须在 1 到 FFFFFFFF 范围内')
        return

    item_code_bytes = item_code.to_bytes(4, byteorder='big')
    encoded_quantity = (0xFFFFFFFF - quantity).to_bytes(4, byteorder='big')
    packet = [
        0, 0, 0, 0x1A, 0x04, 0x65, *str2,
        0, 0, 0x06, 0xB5, 0, 0, 0, 0,
        *item_code_bytes, *encoded_quantity,
    ]
    req = struct.pack('26B', *packet)
    s.send(req)


def fpexp099(s, str2):
    pet_data = _get_pet_bag(s, str2)
    pets = pet_data['pets']
    if not pets:
        print('宠物背包中没有可选择的宠物')
        return

    print('共有%d只宠物' % len(pets))
    for index, listed_pet in enumerate(pets, start=1):
        print('您的第%d个宠物是：%s,等级是%d,转生次数%d,已分配经验%d,转生所需经验%d' % (
            index, listed_pet['nick'], listed_pet['level'], listed_pet['reincarnation_degree'],
            listed_pet['experience'],
            jsexp(listed_pet['reincarnation_degree'], 0) - listed_pet['experience']))

    while True:
        raw_value = input('[请选择要升级到0转99级的精灵，按0退出]').strip()
        try:
            pet_number = int(raw_value)
        except ValueError:
            print('宠物编号必须是整数')
            continue
        if pet_number == 0:
            return
        if 1 <= pet_number <= len(pets):
            break
        print('宠物编号不存在')

    remaining_exp = 60796450
    max_packet_exp = 999999
    selected_pet = pets[pet_number - 1]
    pet_id = selected_pet['pet_id_bytes']
    packet_count = 0
    while remaining_exp > 0:
        packet_exp = min(remaining_exp, max_packet_exp)
        fpexp(packet_exp, pet_id, str2, s)
        remaining_exp -= packet_exp
        packet_count += 1
        if packet_count % 10 == 0 and remaining_exp > 0:
            time.sleep(0.25)
    print('已向宠物%s分配60796449经验' % selected_pet['nick'])

    # 自动分配点数
    packet = [0, 0, 0, 0x16, 0x06, 0x43, *str2, 0, 0, 5,
                         random.randint(0, 255), 0, 0, 0, 0, *pet_id]
    s.send(struct.pack('22B', *packet))

if __name__ == '__main__':
    login_interface('account.txt')

