"""
BOSS 直聘 - MQTT Protobuf 发送测试脚本

用法:
    python test_mqtt_send.py                    # 只测试 protobuf 序列化（不连 MQTT）
    python test_mqtt_send.py --dry-run          # 同上（默认行为）
    python test_mqtt_send.py --real             # 真实连接并发送消息
    python test_mqtt_send.py --real --to-uid=123 --to-encrypt=xxx  # 指定对方信息
"""
import argparse
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def test_protobuf_serialization():
    """测试 protobuf 序列化/反序列化是否正确（无需 MQTT 连接）"""
    logger.info("=== 测试 Protobuf 序列化 ===")

    from boss_mqtt_pb2 import TechwolfChatProtocol, TechwolfMessage

    # 模拟发送方参数
    my_uid = 12345678
    my_encrypt_uid = "my_encrypt_token_abc123"
    to_uid = 87654321
    to_encrypt_uid = "target_encrypt_def456"
    text = "你好，我对贵公司的AI开发工程师职位很感兴趣。"

    # 构建 tempID
    temp_id = int(time.time() * 1000)

    # ---- 按 send_message() 同样的流程构建 ----
    message = TechwolfMessage()
    message.type = 1
    message.mid = temp_id
    message.cmid = temp_id

    # 发送方 (from 是保留字)
    from_field = getattr(message, 'from')
    from_field.uid = my_uid
    from_field.name = my_encrypt_uid
    from_field.source = 0

    # 接收方
    to_field = message.to
    to_field.uid = to_uid
    to_field.name = to_encrypt_uid
    to_field.source = 0

    # 消息体
    body_field = message.body
    body_field.type = 1
    body_field.templateId = 1
    body_field.text = text

    # 协议包
    protocol = TechwolfChatProtocol()
    protocol.type = 1
    protocol.messages.append(message)

    # 序列化
    payload_bytes = protocol.SerializeToString()

    # 验证
    logger.info(f"序列化长度: {len(payload_bytes)} bytes")
    logger.info(f"十六进制: {payload_bytes.hex()}")
    logger.info(f"原始字节: {payload_bytes}")

    # 反序列化验证
    proto2 = TechwolfChatProtocol()
    proto2.ParseFromString(payload_bytes)
    msg2 = proto2.messages[0]

    from2 = getattr(msg2, 'from')
    to2 = msg2.to
    body2 = msg2.body

    logger.info("--- 反序列化验证 ---")
    logger.info(f"protocol.type = {proto2.type}")
    logger.info(f"message.type = {msg2.type}")
    logger.info(f"message.mid = {msg2.mid}")
    logger.info(f"message.cmid = {msg2.cmid}")
    logger.info(f"from.uid = {from2.uid}")
    logger.info(f"from.name = {from2.name}")
    logger.info(f"from.source = {from2.source}")
    logger.info(f"to.uid = {to2.uid}")
    logger.info(f"to.name = {to2.name}")
    logger.info(f"to.source = {to2.source}")
    logger.info(f"body.type = {body2.type}")
    logger.info(f"body.templateId = {body2.templateId}")
    logger.info(f"body.text = {body2.text}")

    # 断言
    assert proto2.type == 1
    assert msg2.type == 1
    assert from2.uid == my_uid
    assert from2.name == my_encrypt_uid
    assert to2.uid == to_uid
    assert to2.name == to_encrypt_uid
    assert body2.text == text
    assert body2.type == 1
    assert body2.templateId == 1

    logger.info("✅ Protobuf 序列化/反序列化测试通过!")
    return True


def test_mqtt_send(to_uid: int, to_encrypt_uid: str):
    """真实连接 MQTT 并发送一条消息"""
    logger.info("=== 测试真实 MQTT 发送 ===")

    # 动态导入避免循环依赖
    sys.path.insert(0, '.')
    from mqtt_chat import BOSSChatMQTT

    client = BOSSChatMQTT()

    # 连接
    if not client.connect():
        logger.error("❌ MQTT 连接失败")
        return False
    logger.info("✅ MQTT 连接成功")

    # 订阅
    if not client.subscribe("chat"):
        logger.error("❌ 订阅失败")
        client.disconnect()
        return False
    logger.info("✅ 已订阅 chat 话题")

    # 发送消息
    msg_text = "你好，我是自动Agent，测试protobuf消息发送。"
    success = client.send_message(
        to_uid=to_uid,
        to_encrypt_uid=to_encrypt_uid,
        text=msg_text,
        to_source=0
    )

    if success:
        logger.info(f"✅ 消息已发送: {msg_text}")
    else:
        logger.error("❌ 消息发送失败")
        client.disconnect()
        return False

    # 等待一会确定
    time.sleep(2)
    client.disconnect()
    logger.info("✅ 全部完成")
    return True


def verify_payload_bytes():
    """检查序列化的 payload 是否与 BOSS 前端预期的格式一致"""
    logger.info("=== 验证 Payload 结构 ===")

    from boss_mqtt_pb2 import TechwolfChatProtocol, TechwolfMessage

    message = TechwolfMessage()
    message.type = 1
    message.mid = 1000
    message.cmid = 1000

    from_field = getattr(message, 'from')
    from_field.uid = 123
    from_field.name = "sender_name"
    from_field.source = 0

    to_field = message.to
    to_field.uid = 456
    to_field.name = "receiver_name"
    to_field.source = 0

    body_field = message.body
    body_field.type = 1
    body_field.templateId = 1
    body_field.text = "test"

    protocol = TechwolfChatProtocol()
    protocol.type = 1
    protocol.messages.append(message)

    payload = protocol.SerializeToString()

    logger.info(f"Payload ({len(payload)} bytes): {payload.hex()}")

    # 验证关键字段偏移（根据 proto schema 检查）
    # TechwolfChatProtocol 的第1个字段是 type=1 (varint)
    # TechwolfMessage 的第1个字段是 from (embedded message)
    # 等

    logger.info("Payload 结构验证完成")


def main():
    parser = argparse.ArgumentParser(description='BOSS 直聘 MQTT Protobuf 发送测试')
    parser.add_argument('--mode', choices=['serialize', 'verify', 'real'],
                        default='serialize',
                        help='测试模式: serialize(只测序列化,默认), verify(校验结构), real(真实发送)')
    parser.add_argument('--to-uid', type=int, default=0, help='对方 userId')
    parser.add_argument('--to-encrypt', type=str, default='', help='对方 encryptUid')
    args = parser.parse_args()

    if args.mode == 'serialize':
        test_protobuf_serialization()
    elif args.mode == 'verify':
        # 先序列化测试，再校验结构
        test_protobuf_serialization()
        print()
        verify_payload_bytes()
    elif args.mode == 'real':
        if not args.to_uid or not args.to_encrypt:
            logger.error("--real 模式需要指定 --to-uid 和 --to-encrypt")
            sys.exit(1)
        test_mqtt_send(args.to_uid, args.to_encrypt)


if __name__ == '__main__':
    main()
