"""大模型可替换层。业务代码只认 gateway。"""
from .gateway import complete, embed, get_client, is_degraded, set_client, usage_summary  # noqa
