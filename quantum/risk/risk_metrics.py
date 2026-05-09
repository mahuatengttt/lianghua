"""
风险指标计算
"""

from datetime import datetime
from typing import List, Optional
import numpy as np
from ..common.models import RiskMetrics, Portfolio


class RiskMetricsCalculator:
    """风险指标计算器"""

    @staticmethod
    def calculate(
        portfolio: Portfolio,
        daily_returns: List[float],
        current_time: datetime,
        peak_capital: float,
    ) -> RiskMetrics:
        """计算当前风险指标快照"""
        ret_arr = np.array(daily_returns) if daily_returns else np.array([0])

        # 当前回撤
        current_dd = 0
        if peak_capital > 0:
            current_dd = (peak_capital - portfolio.total_capital) / peak_capital

        # VaR 95%
        var_95 = 0
        cvar_95 = 0
        if len(ret_arr) > 20:
            var_95 = float(np.percentile(ret_arr, 5))
            downside = ret_arr[ret_arr <= var_95]
            cvar_95 = float(np.mean(downside)) if len(downside) > 0 else var_95

        # 年化波动率
        volatility = float(np.std(ret_arr) * np.sqrt(252)) if len(ret_arr) > 1 else 0

        # 持仓集中度
        total_exposure = portfolio.market_value
        max_pos_pct = 0
        if total_exposure > 0 and portfolio.positions:
            max_pos_pct = max(
                p.market_value / total_exposure
                for p in portfolio.positions.values()
            )

        # 杠杆率
        leverage = total_exposure / portfolio.total_capital if portfolio.total_capital > 0 else 1

        # 夏普
        sharpe = 0
        if len(ret_arr) > 20 and np.std(ret_arr) > 0:
            excess = ret_arr - 0.025 / 252
            sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(252))

        return RiskMetrics(
            timestamp=current_time,
            total_pnl_pct=portfolio.total_pnl_pct,
            current_drawdown=current_dd,
            max_drawdown=0,  # 由外部传入
            var_95=var_95,
            cvar_95=cvar_95,
            volatility=volatility,
            position_concentration=max_pos_pct,
            leverage=leverage,
            sharpe_ratio=sharpe,
            beta=1.0,
            correlation_with_market=0.0,
        )
