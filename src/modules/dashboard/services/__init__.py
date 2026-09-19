"""Dashboard 服务层

承载 API 路由之下的业务装配逻辑（schema 适配、脱敏契约、写路径编排等）。
本子包不得依赖 fastapi——只做纯业务转换，HTTP 语义留在 api 层。
"""
