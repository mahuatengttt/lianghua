"""
Web仪表盘 - 基于Dash
实时显示系统状态、策略表现、风险指标
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

from .metrics import MetricsCollector, SystemMetrics, StrategyMetrics
from ..common.models import Portfolio


class DashboardServer:
    """
    实时监控仪表盘
    使用Dash框架提供Web界面
    """

    def __init__(self, collector: MetricsCollector, port: int = 8050):
        self.collector = collector
        self.port = port
        self._app = None

    def start(self):
        """启动仪表盘"""
        try:
            from dash import Dash, html, dcc, Input, Output
            import plotly.graph_objects as go
            import plotly.express as px

            app = Dash(__name__, title="量子量化交易系统")

            app.layout = html.Div([
                html.H1("⚛️ 量子量化交易系统 - 实时监控", style={"textAlign": "center"}),

                # 系统概览
                html.Div(id="system-overview", children=[
                    html.H2("系统概览"),
                    html.Div(id="system-metrics"),
                ]),

                # 策略表现
                html.Div(id="strategy-panel", children=[
                    html.H2("策略表现"),
                    html.Div(id="strategy-metrics"),
                ]),

                # 自动刷新
                dcc.Interval(id="refresh", interval=5000, n_intervals=0),
            ])

            @app.callback(
                [Output("system-metrics", "children"),
                 Output("strategy-metrics", "children")],
                Input("refresh", "n_intervals"),
            )
            def update_dashboard(n):
                return self._render_system(), self._render_strategies()

            self._app = app
            from threading import Thread
            Thread(target=app.run_server, kwargs={
                "host": "0.0.0.0", "port": self.port, "debug": False,
            }, daemon=True).start()

            return True

        except ImportError:
            return False
        except Exception as e:
            return False

    def _render_system(self) -> html.Div:
        metrics = self.collector.get_latest_metrics()
        sys_metrics = metrics.get("system", {})

        return html.Div([
            html.P(f"运行时间: {sys_metrics.get('uptime_hours', 0):.1f}小时"),
            html.P(f"总资产: ¥{sys_metrics.get('capital', 0):,.2f}"),
            html.P(f"持仓品种: {sys_metrics.get('positions', 0)}"),
            html.P(f"活跃策略: {sys_metrics.get('strategies', 0)}"),
            html.P(f"信号速率: {sys_metrics.get('signals_per_min', 0)}/min"),
            html.P(f"订单速率: {sys_metrics.get('orders_per_min', 0)}/min"),
        ])

    def _render_strategies(self) -> html.Div:
        metrics = self.collector.get_latest_metrics()
        strategies = metrics.get("strategies", {})

        if not strategies:
            return html.P("暂无策略数据")

        cards = []
        for name, data in strategies.items():
            cards.append(html.Div([
                html.H3(name),
                html.P(f"总信号: {data.get('total_signals', 0)}"),
                html.P(f"总交易: {data.get('total_trades', 0)}"),
                html.P(f"胜率: {data.get('win_rate', 0)*100:.1f}%"),
                html.P(f"总收益: {data.get('total_return', 0)*100:.2f}%"),
            ]))

        return html.Div(cards)

    def stop(self):
        """停止仪表盘"""
        pass
