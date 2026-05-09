"""
回测报告生成器 - HTML格式
"""

from typing import List
from ..common.models import BacktestResult, Trade
from ..common.enums import OrderSide
from .config import BacktestEngineConfig


class BacktestReport:
    """回测报告生成"""

    def generate(self, result: BacktestResult, config: BacktestEngineConfig) -> str:
        """生成HTML报告"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>量子 - 回测报告</title>
            <style>
                body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; margin-bottom: 5px; }}
                h2 {{ color: #555; font-size: 18px; margin-top: 0; }}
                .metrics {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }}
                .metric {{ text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px; }}
                .metric .value {{ font-size: 24px; font-weight: bold; }}
                .metric .label {{ font-size: 12px; color: #888; margin-top: 5px; }}
                .positive {{ color: #e74c3c; }}
                .negative {{ color: #27ae60; }}
                table {{ width: 100%; border-collapse: collapse; }}
                th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
                th {{ background: #f8f9fa; font-weight: 600; }}
                .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
                .tag-buy {{ background: #ffe0e0; color: #c0392b; }}
                .tag-sell {{ background: #d4edda; color: #27ae60; }}
            </style>
            <!-- Chart.js for equity curve -->
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        </head>
        <body>
            <div class="container">
                <div class="card">
                    <h1>📊 量子回测报告</h1>
                    <p style="color:#888;">策略: {result.strategy_name} | 期间: {result.start_date} ~ {result.end_date}</p>
                </div>
        """

        # 核心指标
        html += self._render_metrics(result, config)

        # 权益曲线
        html += self._render_equity_chart(result)

        # 交易列表
        html += self._render_trades(result.trades)

        html += """
            </div>
        </body>
        </html>
        """
        return html

    def _render_metrics(self, result: BacktestResult, config: BacktestEngineConfig) -> str:
        """渲染核心指标"""
        def fmt(v, pct=False):
            if pct:
                return f"{v*100:.2f}%"
            return f"{v:,.2f}"

        return f"""
        <div class="card">
            <h2>核心指标</h2>
            <div class="metrics">
                <div class="metric">
                    <div class="value {'positive' if result.total_return >= 0 else 'negative'}">{fmt(result.total_return, True)}</div>
                    <div class="label">总收益率</div>
                </div>
                <div class="metric">
                    <div class="value {'positive' if result.annual_return >= 0 else 'negative'}">{fmt(result.annual_return, True)}</div>
                    <div class="label">年化收益率</div>
                </div>
                <div class="metric">
                    <div class="value negative">{fmt(result.max_drawdown, True)}</div>
                    <div class="label">最大回撤</div>
                </div>
                <div class="metric">
                    <div class="value">{result.sharpe_ratio:.2f}</div>
                    <div class="label">夏普比率</div>
                </div>
                <div class="metric">
                    <div class="value">{result.sortino_ratio:.2f}</div>
                    <div class="label">索提诺比率</div>
                </div>
                <div class="metric">
                    <div class="value">{result.calmar_ratio:.2f}</div>
                    <div class="label">卡玛比率</div>
                </div>
                <div class="metric">
                    <div class="value">{fmt(result.win_rate, True)}</div>
                    <div class="label">胜率</div>
                </div>
                <div class="metric">
                    <div class="value">{result.profit_factor:.2f}</div>
                    <div class="label">盈亏比</div>
                </div>
                <div class="metric">
                    <div class="value">{result.total_trades}</div>
                    <div class="label">总交易次数</div>
                </div>
                <div class="metric">
                    <div class="value">{result.avg_holding_period:.1f}</div>
                    <div class="label">平均持仓(天)</div>
                </div>
                <div class="metric">
                    <div class="value">¥{fmt(config.initial_capital)}</div>
                    <div class="label">初始资金</div>
                </div>
                <div class="metric">
                    <div class="value">¥{fmt(result.final_capital)}</div>
                    <div class="label">最终资金</div>
                </div>
            </div>
        </div>
        """

    def _render_equity_chart(self, result: BacktestResult) -> str:
        """渲染权益曲线图表"""
        if not result.equity_curve:
            return ""

        equity_data = list(result.equity_curve)
        labels = list(range(len(equity_data)))

        return f"""
        <div class="card">
            <h2>权益曲线</h2>
            <canvas id="equityChart" height="300"></canvas>
            <script>
                new Chart(document.getElementById('equityChart'), {{
                    type: 'line',
                    data: {{
                        labels: {labels},
                        datasets: [{{
                            label: '总资产',
                            data: {equity_data},
                            borderColor: '#3498db',
                            backgroundColor: 'rgba(52,152,219,0.1)',
                            fill: true,
                            tension: 0.1,
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        plugins: {{ legend: {{ display: false }} }},
                        scales: {{
                            y: {{ ticks: {{ callback: v => '¥' + v.toLocaleString() }} }}
                        }}
                    }}
                }});
            </script>
        </div>
        """

    def _render_trades(self, trades: List[Trade]) -> str:
        """渲染交易记录"""
        if not trades:
            return ""

        rows = ""
        for i, t in enumerate(trades):
            tag_class = "tag-buy" if t.side == OrderSide.BUY else "tag-sell"
            rows += f"""
            <tr>
                <td>{i+1}</td>
                <td><span class="tag {tag_class}">{t.side.value}</span></td>
                <td>{t.symbol}</td>
                <td>{t.price:.2f}</td>
                <td>{t.quantity}</td>
                <td>{t.amount:.2f}</td>
                <td>{t.commission:.2f}</td>
                <td>{t.trade_time}</td>
            </tr>
            """

        return f"""
        <div class="card">
            <h2>交易记录 ({len(trades)}笔)</h2>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>#</th><th>方向</th><th>标的</th><th>价格</th>
                            <th>数量</th><th>金额</th><th>佣金</th><th>时间</th>
                        </tr>
                    </thead>
                    <tbody>{rows}</tbody>
                </table>
            </div>
        </div>
        """
