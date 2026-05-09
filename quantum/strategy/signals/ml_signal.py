"""
机器学习预测信号生成器 - 基于XGBoost/LSTM
"""

from typing import List, Optional, Dict, Any
import numpy as np

from ..base import SignalGenerator
from ...common.models import Bar, Signal
from ...common.enums import SignalAction


class MLSignalGenerator(SignalGenerator):
    """
    机器学习信号生成器
    支持多种模型：xgboost / random_forest / lstm
    核心流程：特征工程 → 模型预测 → 信号转换
    """

    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__("ml_predictor", parameters or {})
        self.model_type = self.get_param("model_type", "xgboost")
        self.model_path = self.get_param("model_path", "")
        self.feature_window = self.get_param("feature_window", 20)
        self.prediction_horizon = self.get_param("prediction_horizon", 1)
        self.confidence_threshold = self.get_param("confidence_threshold", 0.55)
        self._model = None
        self._scaler = None

    def compute(self, bars: List[Bar]) -> List[Signal]:
        if len(bars) < self.feature_window + self.prediction_horizon + 5:
            return []

        # 加载/训练模型（懒加载）
        if self._model is None:
            self._load_or_train_model(bars)

        if self._model is None:
            return []

        # 特征工程
        features = self._build_features(bars)
        if features is None:
            return []

        # 预测
        try:
            prediction = self._predict(features)
        except Exception:
            return []

        if prediction is None:
            return []

        symbol = bars[-1].symbol
        bar = bars[-1]
        signals = []

        if len(prediction.shape) == 1:
            pred_value = float(prediction[-1])
        else:
            pred_value = float(prediction[-1, 0])

        # 分类模型输出（0=跌, 1=涨）
        if pred_value > self.confidence_threshold:
            confidence = min(1.0, (pred_value - 0.5) * 2)
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.OPEN_LONG,
                price=bar.close,
                confidence=confidence,
                strategy_name=f"ML_{self.model_type.upper()}",
                reason=f"ML模型预测上涨概率: {pred_value:.1%}",
                metadata={"pred_prob": pred_value, "model": self.model_type},
            ))
        elif pred_value < 1 - self.confidence_threshold:
            confidence = min(1.0, (0.5 - pred_value) * 2)
            signals.append(Signal(
                timestamp=bar.time,
                symbol=symbol,
                action=SignalAction.CLOSE_LONG,
                price=bar.close,
                confidence=confidence,
                strategy_name=f"ML_{self.model_type.upper()}",
                reason=f"ML模型预测下跌概率: {1-pred_value:.1%}",
                metadata={"pred_prob": pred_value, "model": self.model_type},
            ))

        return signals

    def _build_features(self, bars: List[Bar]) -> Optional[np.ndarray]:
        """构建特征矩阵"""
        recent = bars[-(self.feature_window + 5):]
        closes = np.array([b.close for b in recent])
        highs = np.array([b.high for b in recent])
        lows = np.array([b.low for b in recent])
        volumes = np.array([b.volume for b in recent])

        features = []

        for i in range(self.feature_window, len(recent)):
            window_close = closes[i - self.feature_window:i + 1]
            window_high = highs[i - self.feature_window:i + 1]
            window_low = lows[i - self.feature_window:i + 1]
            window_vol = volumes[i - self.feature_window:i + 1]

            f = self._extract_features(window_close, window_high, window_low, window_vol)
            features.append(f)

        return np.array(features) if features else None

    def _extract_features(
        self, closes: np.ndarray, highs: np.ndarray,
        lows: np.ndarray, volumes: np.ndarray,
    ) -> np.ndarray:
        """提取特征向量"""
        features = []

        # 1. 收益率特征
        returns = np.diff(closes) / closes[:-1]
        features.extend([
            np.mean(returns[-5:]),
            np.mean(returns[-10:]),
            np.mean(returns[-20:]) if len(returns) >= 20 else 0,
            np.std(returns[-10:]),
            returns[-1] if len(returns) > 0 else 0,
        ])

        # 2. 价格位置特征
        hh = np.max(highs)
        ll = np.min(lows)
        features.append((closes[-1] - ll) / (hh - ll) if hh > ll else 0.5)

        # 3. 均线特征
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else ma5
        ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else ma5
        features.extend([
            closes[-1] / ma5 - 1,
            ma5 / ma10 - 1,
            ma10 / ma20 - 1 if ma20 > 0 else 0,
        ])

        # 4. 波动率特征
        features.append(np.std(returns[-10:]) * np.sqrt(252))

        # 5. 成交量特征
        avg_vol = np.mean(volumes)
        features.append(volumes[-1] / avg_vol if avg_vol > 0 else 1)

        # 6. 动量特征
        features.extend([
            closes[-1] / closes[-5] - 1 if len(closes) >= 5 else 0,
            closes[-1] / closes[-10] - 1 if len(closes) >= 10 else 0,
            closes[-1] / closes[-20] - 1 if len(closes) >= 20 else 0,
        ])

        # 7. RSI
        from ...common.utils import IndicatorUtils
        rsi = IndicatorUtils.rsi(closes)
        features.append(rsi[-1] / 100 if not np.isnan(rsi[-1]) else 0.5)

        # 8. MACD
        dif, dea, hist = IndicatorUtils.macd(closes)
        features.extend([
            float(dif[-1]) if not np.isnan(dif[-1]) else 0,
            float(hist[-1]) if not np.isnan(hist[-1]) else 0,
        ])

        return np.array(features, dtype=np.float32)

    def _load_or_train_model(self, bars: List[Bar]):
        """加载或训练模型"""
        try:
            if self.model_path:
                import joblib
                self._model = joblib.load(self.model_path)
                return
        except Exception:
            pass

        # 无预训练模型，使用简单逻辑回归作为fallback
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.preprocessing import StandardScaler

            # 构建训练数据
            X_list, y_list = [], []
            closes = np.array([b.close for b in bars])

            for i in range(self.feature_window + self.prediction_horizon, len(bars)):
                window_closes = closes[i - self.feature_window - self.prediction_horizon:i + 1 - self.prediction_horizon]
                window_highs = np.array([b.high for b in bars])[i - self.feature_window - self.prediction_horizon:i + 1 - self.prediction_horizon]
                window_lows = np.array([b.low for b in bars])[i - self.feature_window - self.prediction_horizon:i + 1 - self.prediction_horizon]
                window_volumes = np.array([b.volume for b in bars])[i - self.feature_window - self.prediction_horizon:i + 1 - self.prediction_horizon]
                window_closes_full = closes[i - self.feature_window - self.prediction_horizon:i + 1 - self.prediction_horizon]

                if len(window_closes) < self.feature_window + 1:
                    continue

                f = self._extract_features(window_closes, window_highs, window_lows, window_volumes)
                f_numeric = np.array([x if np.isfinite(x) else 0 for x in f])
                X_list.append(f_numeric)

                # 标签：未来收益是否为正
                future_ret = (closes[i] - closes[i - self.prediction_horizon]) / closes[i - self.prediction_horizon]
                y_list.append(1 if future_ret > 0 else 0)

            if len(X_list) > 50:
                X = np.array(X_list)
                y = np.array(y_list)

                # 标准化
                self._scaler = StandardScaler()
                X_scaled = self._scaler.fit_transform(X)

                # 训练模型
                self._model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=8,
                    random_state=42,
                    n_jobs=-1,
                )
                self._model.fit(X_scaled, y)
        except ImportError:
            pass  # fallback模型也可用

    def _predict(self, features: np.ndarray) -> Optional[np.ndarray]:
        """执行预测"""
        if self._model is None:
            return None

        # 标准化（如果有）
        X = features
        if self._scaler is not None:
            X = self._scaler.transform(features)

        # 获取预测概率
        if hasattr(self._model, "predict_proba"):
            probs = self._model.predict_proba(X)
            if probs.shape[1] > 1:
                return probs[:, 1]
            else:
                return probs[:, 0]
        else:
            return self._model.predict(X)


class LSTMPredictor(SignalGenerator):
    """
    LSTM深度学习预测信号
    使用PyTorch构建简单LSTM模型
    """

    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__("lstm_predictor", parameters or {})
        self.seq_length = self.get_param("seq_length", 20)
        self.hidden_size = self.get_param("hidden_size", 64)
        self.num_layers = self.get_param("num_layers", 2)
        self._model = None

    def compute(self, bars: List[Bar]) -> List[Signal]:
        """LSTM预测（需要PyTorch）"""
        try:
            import torch
            import torch.nn as nn

            if self._model is None:
                self._build_model()

            if self._model is None:
                return []

            closes = np.array([b.close for b in bars])
            if len(closes) < self.seq_length + 5:
                return []

            # 归一化
            recent = closes[-self.seq_length:]
            mean, std = np.mean(closes[-(self.seq_length * 3):]), np.std(closes[-(self.seq_length * 3):])
            if std == 0:
                return []
            normalized = (recent - mean) / std

            # 预测
            with torch.no_grad():
                x = torch.FloatTensor(normalized).view(1, 1, -1)
                pred_normalized = self._model(x).item()
                pred_price = pred_normalized * std + mean

            bar = bars[-1]
            symbol = bar.symbol
            direction = pred_price - bar.close
            confidence = min(1.0, abs(direction) / bar.close * 10)

            signals = []
            if direction > 0 and confidence > 0.3:
                signals.append(Signal(
                    timestamp=bar.time, symbol=symbol,
                    action=SignalAction.OPEN_LONG, price=bar.close,
                    confidence=confidence,
                    strategy_name="LSTM_Predictor",
                    reason=f"LSTM预测上涨: target={pred_price:.2f}",
                    metadata={"pred_price": pred_price, "model": "lstm"},
                ))
            elif direction < 0 and confidence > 0.3:
                signals.append(Signal(
                    timestamp=bar.time, symbol=symbol,
                    action=SignalAction.CLOSE_LONG, price=bar.close,
                    confidence=confidence,
                    strategy_name="LSTM_Predictor",
                    reason=f"LSTM预测下跌: target={pred_price:.2f}",
                    metadata={"pred_price": pred_price, "model": "lstm"},
                ))

            return signals

        except ImportError:
            return []
        except Exception:
            return []

    def _build_model(self):
        """构建LSTM模型"""
        try:
            import torch
            import torch.nn as nn

            class SimpleLSTM(nn.Module):
                def __init__(self, input_size, hidden_size, num_layers):
                    super().__init__()
                    self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
                    self.fc = nn.Linear(hidden_size, 1)

                def forward(self, x):
                    out, _ = self.lstm(x)
                    return self.fc(out[:, -1, :])

            self._model = SimpleLSTM(1, self.hidden_size, self.num_layers)
            self._model.eval()
        except Exception:
            self._model = None
