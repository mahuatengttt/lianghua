"""
LSTM机器学习预测策略
"""

from typing import Optional

from ..base import BaseStrategy
from ..signals.ml_signal import MLSignalGenerator
from ...common.models import Bar, Signal, StrategyConfig
from ...common.enums import TimeFrame, StrategyCategory


class LSTMPredictorStrategy(BaseStrategy):
    """
    LSTM深度学习预测策略
    使用历史价格序列预测未来涨跌
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="LSTM_Predictor",
                category=StrategyCategory.ML_PREDICT,
                symbols=[],
                parameters={
                    "model_type": "xgboost",
                    "feature_window": 20,
                    "prediction_horizon": 1,
                    "confidence_threshold": 0.55,
                    "model_path": "",
                },
                timeframe=TimeFrame.DAILY,
            )
        super().__init__(config)
        self.signal_gen = MLSignalGenerator({
            "model_type": config.parameters.get("model_type", "xgboost"),
            "feature_window": config.parameters.get("feature_window", 20),
            "prediction_horizon": config.parameters.get("prediction_horizon", 1),
            "confidence_threshold": config.parameters.get("confidence_threshold", 0.55),
            "model_path": config.parameters.get("model_path", ""),
        })

    def setup(self):
        self.log(f"ML预测策略初始化: {self.signal_gen.model_type}")
        self.log(f"特征窗口: {self.signal_gen.feature_window}, 预测周期: {self.signal_gen.prediction_horizon}")

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        symbol = bar.symbol
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
        self.bar_history[symbol].append(bar)

        bars = self.bar_history[symbol]
        if len(bars) < self.signal_gen.feature_window + 10:
            return None

        signals = self.signal_gen.compute(bars)
        return signals[-1] if signals else None

    def teardown(self):
        self.log("ML预测策略停止")
