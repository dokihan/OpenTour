from __future__ import annotations

from booster.izumo.codec import pack_mql_frame
from booster.izumo.crypto import IzumoCrypt

class MqlSession:
    def __init__(self, crypt: IzumoCrypt | None):
        self.crypt = crypt
        self.tx_seq = 0
        self.rx_next = 0
        self.synced = False

    def sync(self, seq: int, ack: int) -> bool:
        if self.synced:
            return False
        self.synced = True
        resumed = (seq, ack) != (self.rx_next, self.tx_seq)
        self.rx_next, self.tx_seq = seq, ack
        return resumed

    def accept(self, seq: int) -> bool:
        if seq != self.rx_next:
            return False
        self.rx_next = (self.rx_next + 1) & 0xFF
        return True

    def data_frame(self, plain: bytes, channel: int = 0) -> bytes:
        payload = self.crypt.encrypt(plain)
        frame = pack_mql_frame(self.tx_seq, self.rx_next, channel, payload)
        self.tx_seq = (self.tx_seq + 1) & 0xFF
        return frame

    def keepalive(self) -> bytes:
        return pack_mql_frame(self.tx_seq, self.rx_next, 0, b"")
