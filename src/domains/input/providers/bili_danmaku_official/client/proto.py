# src/domains/input/providers/bili_danmaku_official/client/proto.py

import logging
import struct


class Proto:
    """Bilibili WebSocket 协议处理器"""

    def __init__(self):
        self.packet_len = 0
        self.header_len = 16
        self.ver = 0
        self.op = 0
        self.seq = 0
        self.body = ""
        self.max_body = 2048
        self.logger = logging.getLogger(__name__)

    def pack(self) -> bytes:
        """打包消息"""
        self.packet_len = len(self.body.encode()) + self.header_len
        buf = struct.pack(">i", self.packet_len)
        buf += struct.pack(">h", self.header_len)
        buf += struct.pack(">h", self.ver)
        buf += struct.pack(">i", self.op)
        buf += struct.pack(">i", self.seq)
        buf += self.body.encode()
        return buf

    def unpack(self, buf: bytes):
        """解包消息"""
        try:
            if len(buf) < self.header_len:
                self.logger.warning("包头长度不够")
                return

            self.packet_len = struct.unpack(">i", buf[0:4])[0]
            self.header_len = struct.unpack(">h", buf[4:6])[0]
            self.ver = struct.unpack(">h", buf[6:8])[0]
            self.op = struct.unpack(">i", buf[8:12])[0]
            self.seq = struct.unpack(">i", buf[12:16])[0]

            if self.packet_len < 0 or self.packet_len > self.max_body:
                self.logger.warning(f"包体长度异常: {self.packet_len}, 最大长度: {self.max_body}")
                return

            if self.header_len != 16:
                self.logger.warning(f"包头长度异常: {self.header_len}")
                return

            body_len = self.packet_len - self.header_len
            if body_len <= 0:
                self.body = ""
                return

            self.body = buf[16 : self.packet_len].decode("utf-8")

        except Exception as e:
            self.logger.error(f"解包消息时发生错误: {e}")
            self.body = ""

    def get_message_type(self) -> str:
        """根据操作码获取消息类型"""
        op_types = {2: "heartbeat", 3: "heartbeat_reply", 5: "notification", 7: "auth", 8: "auth_reply"}
        return op_types.get(self.op, "unknown")

    def is_valid(self) -> bool:
        """检查消息是否有效"""
        return self.packet_len > 0 and self.header_len == 16 and self.packet_len <= self.max_body
