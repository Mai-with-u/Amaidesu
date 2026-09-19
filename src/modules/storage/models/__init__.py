"""被持久化实体的数据契约子包。

存放 rundowns 等存储表对应的数据模型（Pydantic 契约）：仓储据此序列化 /
反序列化，Agent 与 Dashboard 等消费方依赖方向一律向下（agents → modules、
dashboard → storage），本包不反向依赖任何上层。
"""
