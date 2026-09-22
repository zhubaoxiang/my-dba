"""
数据源凭据的可逆加解密

登录密码走 CommonUtils.password_encrypt（MD5 单向哈希，不可逆）；
数据源凭据必须能还原出明文才能回连目标库，故单独实现可逆加密。
密钥取自 conf.ini 的 [datasource] secret_key，缺失或非法时抛错，禁止回退明文。
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from utils.configure import CONF_ATTR

_SECRET_KEY_ITEM = "datasource_secret_key"


class CryptoKeyError(Exception):
    """
    数据源加密密钥缺失、非法，或密文无法解密
    """


def _build_cipher() -> Fernet:
    secret = (CONF_ATTR.get(_SECRET_KEY_ITEM) or "").strip()
    if not secret:
        raise CryptoKeyError(
            f"未配置数据源加密密钥，请在 conf.ini 的 [datasource] 段设置 secret_key（配置键：{_SECRET_KEY_ITEM}）"
        )

    # 配置值是任意字符串，用 SHA-256 派生出 Fernet 要求的 32 字节 urlsafe base64 密钥
    derived = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plain: str) -> str:
    """
    加密明文，空值返回空串
    """
    if not plain:
        return ""
    return _build_cipher().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt(cipher_text: str) -> str:
    """
    解密密文，失败时抛错而不是退回原文
    """
    if not cipher_text:
        return ""
    try:
        return _build_cipher().decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoKeyError("数据源凭据解密失败：密钥不匹配或密文已损坏") from exc
