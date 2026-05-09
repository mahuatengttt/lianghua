"""
绩效分析器
"""

from typing import List
from datetime import datetime
import numpy as np

from dataclasses import dataclass

from ..common.models import Trade, Portfolio, BacktestResult
from ..common.enums import OrderSide
from ..common.utils import MathUtils


@dataclass
class _AnalyzerConfig:
    """绩效分析配置"""
    initial_capital: float = 1_000_000.0
    start_date = None
    end_date = None


class PerformanceAnalyzer:
    """回测绩效分析器"""

    def analyze(
        self,
        portfolio: Portfolio,
        trades: List[Trade],
        equity_curve: List[float],
        daily_returns: List[float],
        config,
    ) -> BacktestResult:
        """综合分析"""
        # 基础统计
        final_capital = portfolio.total_capital
        total_return = (final_capital - config.initial_capital) / config.initial_capital

        # 交易统计
        total_trades = len(trades)
        win_trades, lose_trades = 0, 0
        trade_pnls = []

        # 按信号分组（同订单多次成交合并）
        trade_groups = {}
        for t in trades:
            key = t.order_id
            if key not in trade_groups:
                trade_groups[key] = []
            trade_groups[key].append(t)

        # 计算每笔交易的盈亏
        for order_id, group in trade_groups.items():
            buy_trades = [t for t in group if t.side in [OrderSide.BUY, OrderSide.BUY_COVER]]
            sell_trades = [t for t in group if t.side in [OrderSide.SELL, OrderSide.SELL_SHORT]]

            if buy_trades and sell_trades:
                # 完整的一买一卖配对
                buy_amt = sum(t.amount + t.commission + t.tax for t in buy_trades)
                sell_amt = sum(t.amount - t.commission - t.tax for t in sell_trades)
                pnl = sell_amt - buy_amt
                trade_pnls.append(pnl)

        # 如果引擎有收盘强平，平仓交易也计入盈亏
        if not trade_pnls and len(trades) >= 2:
            # 尝试按买卖交替配对
            buys = [t for t in trades if t.side in [OrderSide.BUY, OrderSide.BUY_COVER]]
            sells = [t for t in trades if t.side in [OrderSide.SELL, OrderSide.SELL_SHORT]]
            for i in range(min(len(buys), len(sells))):
                buy_amt = buys[i].amount + buys[i].commission + buys[i].tax
                sell_amt = sells[i].amount - sells[i].commission - sells[i].tax
                trade_pnls.append(sell_amt - buy_amt)

        win_trades = sum(1 for p in trade_pnls if p > 0)
        lose_trades = sum(1 for p in trade_pnls if p <= 0)

        # 时间跨度
        dates = [t.trade_time for t in trades]
        if dates and len(dates) >= 2:
            trading_days = (max(dates) - min(dates)).days
        elif equity_curve:
            trading_days = len(equity_curve)
        else:
            trading_days = 1

        years = max(trading_days / 252, 1 / 252)
        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

        # 风险指标
        daily_ret_arr = np.array(daily_returns) if daily_returns else np.array([0])
        max_dd = MathUtils.max_drawdown(equity_curve) if equity_curve else 0
        sharpe = MathUtils.sharpe_ratio(daily_returns) if daily_returns else 0
        sortino = MathUtils.sortino_ratio(daily_returns) if daily_returns else 0
        calmar = annual_return / max_dd if max_dd > 0 else 0

        # 胜率/盈亏比
        win_rate = win_trades / max(total_trades, 1)
        avg_win = np.mean([p for p in trade_pnls if p > 0]) if win_trades > 0 else 0
        avg_loss = abs(np.mean([p for p in trade_pnls if p < 0])) if lose_trades > 0 else 1
        profit_factor = avg_win * win_trades / (avg_loss * lose_trades) if lose_trades > 0 and avg_loss > 0 else float('inf')

        # 月度收益
        monthly_returns = self._calc_monthly_returns(equity_curve, dates)

        # 日期兜底
        dates = dates or []
        start_dt = config.start_date or (dates[0] if dates else portfolio.updated_at or datetime(2024, 1, 1))
        end_dt = config.end_date or (dates[-1] if dates else portfolio.updated_at or datetime.now())

        return BacktestResult(
            strategy_name="Strategy",
            start_date=start_dt,
            end_date=end_dt,
            initial_capital=config.initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            annual_return=annual_return,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            win_rate=win_rate,
            profit_factor=profit_factor if profit_factor != float('inf') else 999.0,
            total_trades=total_trades,
            winning_trades=win_trades,
            losing_trades=lose_trades,
            avg_holding_period=trading_days / max(total_trades, 1) if total_trades > 0 else 0,
            avg_trade_return=np.mean(trade_pnls) / config.initial_capital if trade_pnls else 0,
            daily_returns=daily_returns,
            equity_curve=equity_curve,
            trades=trades,
            monthly_returns=monthly_returns,
        )

    def _calc_monthly_returns(self, equity_curve: List[float], dates: List) -> dict:
        """计算月度收益率"""
        if not equity_curve or len(equity_curve) < 2:
            return {}

        try:
            import pandas as pd
            df = pd.DataFrame({"equity": equity_curve})
            if dates and len(dates) == len(equity_curve):
                df.index = pd.to_datetime(dates)
            else:
                df.index = pd.date_range(
                    end=pd.Timestamp.now(),
                    periods=len(equity_curve),
                    freq="D",
                )
            monthly = df.resample("ME").last().pct_change().dropna()
            return {str(k.strftime("%Y-%m")): float(v) for k, v in monthly["equity"].items()}
        except Exception:
            return {}
