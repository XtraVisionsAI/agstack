#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

from jwcrypto import jwk, jws
from passlib.context import CryptContext


# bcrypt 上下文配置：12 轮加密，提供良好的安全性和性能平衡
__context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

__all__ = [
    "create_jwt_token_with_key",
    "verify_jwt_token_with_key",
    "verify_password_with_key",
    "hash_password_with_key",
]


def create_jwt_token_with_key(subject: dict, secret_key: str, expires: int) -> tuple[str, datetime]:
    """创建 JWT Token

    使用 HS256 算法签名，将 subject 数据编码到 JWT 的 sub 字段中。

    :param subject: 要编码的数据字典（如 user_id, uniquier 等）
    :param secret_key: JWT 签名密钥
    :param expires: 过期时间（分钟）
    :return: (JWT token 字符串, 过期时间)
    """
    expires_delta = datetime.now() + timedelta(minutes=expires)

    # 创建 JWK 密钥对象
    key = jwk.JWK(kty="oct", k=secret_key)

    # 构建 payload
    payload = {
        "iat": int(time.time()),  # 签发时间
        "exp": int(expires_delta.timestamp()),  # 过期时间
        "sub": json.dumps(subject, ensure_ascii=False),  # 主题数据
    }

    # 创建并签名 JWT
    token = jws.JWS(json.dumps(payload))
    token.add_signature(key, alg="HS256", protected={"alg": "HS256", "typ": "JWT"})

    return token.serialize(compact=True), expires_delta


def verify_jwt_token_with_key(token: str, secret_key: str) -> dict | None:
    """验证 JWT Token 并返回 payload

    验证 Token 签名和过期时间，成功则返回 subject 数据。

    :param token: JWT token 字符串
    :param secret_key: JWT 签名密钥（必须与创建时使用的密钥一致）
    :return: subject 数据字典，验证失败或已过期返回 None

    验证失败的情况：
    - Token 格式错误
    - 签名验证失败
    - Token 已过期
    """
    try:
        key = jwk.JWK(kty="oct", k=secret_key)
        jws_token = jws.JWS()
        jws_token.deserialize(token)
        jws_token.verify(key)

        # 解析 payload
        payload = json.loads(jws_token.payload.decode("utf-8"))

        # 检查过期时间
        if "exp" in payload and payload["exp"] < time.time():
            return None

        # 解析 subject（返回创建时传入的 subject 数据）
        if "sub" in payload:
            subject = json.loads(payload["sub"])
            return subject

        return payload
    except Exception:  # noqa
        return None


def hash_password_with_key(passwd: str, secret_key: str) -> str:
    """哈希密码

    使用 HMAC-SHA512 + bcrypt 双重保护：
    1. 先用 HMAC-SHA512 对密码加盐，防止 bcrypt 的 72 字节长度限制
    2. 再用 bcrypt 哈希，提供慢速哈希保护，防止暴力破解

    :param passwd: 明文密码
    :param secret_key: HMAC 密钥（应用级密钥，不同于用户密码）
    :return: bcrypt 哈希字符串
    """
    passwd = _get_hmac_str(passwd, secret_key)
    hashed = __context.hash(passwd)

    return hashed


def verify_password_with_key(passwd: str, hashed_pwd: str, secret_key: str) -> bool:
    """验证密码

    使用与 hash_password_with_key 相同的流程验证密码。

    :param passwd: 待验证的明文密码
    :param hashed_pwd: 存储的哈希密码（由 hash_password_with_key 生成）
    :param secret_key: HMAC 密钥（必须与哈希时使用的密钥一致）
    :return: 密码正确返回 True，否则返回 False
    """
    passwd = _get_hmac_str(passwd, secret_key)
    return __context.verify(passwd, hashed_pwd)


def _get_hmac_str(plaintext: str, secret_key: str) -> str:
    """使用 HMAC-SHA512 对文本加盐

    内部辅助函数，用于密码哈希前的预处理。

    :param plaintext: 明文
    :param secret_key: HMAC 密钥
    :return: Base64 编码的 HMAC 结果
    """
    h = hmac.new(secret_key.encode("utf-8"), plaintext.encode("utf-8"), hashlib.sha512)
    return base64.b64encode(h.digest()).decode("ascii")
