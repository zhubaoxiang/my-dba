"""
首页总览视图

只读、不落库，且不承载任何资源，因此挂在 `baseviews.StatelessView` 上。

同基线：不做应用层鉴权，访问控制依赖网络隔离。响应**不含任何凭据**——
数据源密码与模型 API Key 都不会出现在这里。
"""

from apps.base import baseviews
from apps.overview import services


class OverviewView(baseviews.StatelessView):
    """
    首页总览：系统就绪状态、各数据源与各知识库的状态

    一次返回首页所需的全部数据，避免前端分别请求多次（其中「每个库的问题数」
    若由前端拼装会变成 N+1）。
    """

    def list(self, request, **kwargs):
        return baseviews.ResponseOK(services.build_overview())
