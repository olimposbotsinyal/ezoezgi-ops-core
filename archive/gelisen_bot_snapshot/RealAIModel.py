# RealAIModel.py

from __future__ import annotations

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import accuracy_score, classification_report

import xgboost as xgb
import lightgbm as lgb

import json
import numpy as np
import pandas as pd

from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone
import logging
import os
import joblib


# Logging konfigürasyonu
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class RealAIModel:
    """
    Gelişmiş Makine Öğrenmesi Modelleme Sınıfı

    Bu sınıf, çoklu makine öğrenmesi modellerinin eğitimi,
    tahmini ve yönetimi için tasarlanmıştır.
    """

    def __init__(
        self,
        model_directory: str = 'models',
        log_level: int = logging.INFO
    ):
        # Dizin ayarları
        self.models_dir = model_directory
        os.makedirs(self.models_dir, exist_ok=True)

        # Logging
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)

        # Model yapıları
        self.models: Dict[str, Any] = {}
        self.scaler: Optional[StandardScaler] = None

        # Feature name desteği (sklearn uyarılarını bitirir)
        self.feature_names: Optional[List[str]] = None

        # Eğitim durumu
        self.is_trained: bool = False
        self.training_metadata: Dict[str, Any] = {}

        # Model başlatma
        self._initialize_models()

        self.degraded_mode: bool = False

    # ---------------------------------------------------------------------
    # Internal helpers (exchange/metadata/feature names)
    # ---------------------------------------------------------------------
    @staticmethod
    def _exchange_norm(exchange: Optional[str]) -> str:
        return str(exchange or "").strip().lower()

    def _meta_path(self) -> str:
        return os.path.join(self.models_dir, "metadata.json")

    def _read_metadata_file(self) -> Dict[str, Any]:
        path = self._meta_path()
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self.logger.warning(f"metadata.json okunamadı/bozuk: {e}")
            return {}

    def _write_metadata_file(self, all_metadata: Dict[str, Any]) -> bool:
        try:
            path = self._meta_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(all_metadata, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            self.logger.error(f"metadata.json yazılamadı: {e}")
            return False

    def _load_exchange_metadata(self, exchange: str) -> Dict[str, Any]:
        ex = self._exchange_norm(exchange)
        all_meta = self._read_metadata_file()
        # hem lower hem upper ihtimali
        ex_meta = all_meta.get(ex) or all_meta.get(ex.upper()) or all_meta.get(ex.lower()) or {}
        return ex_meta if isinstance(ex_meta, dict) else {}

    def _to_feature_df(self, x: np.ndarray) -> pd.DataFrame:
        """
        Model fit edilirken kullanılan kolon isimleriyle aynı kolonları garanti eder.
        x: shape (n_samples, n_features)
        """
        if x is None:
            return pd.DataFrame()

        x = np.asarray(x)
        if x.ndim == 1:
            x = x.reshape(1, -1)

        n_feat = int(x.shape[1])
        cols = self.feature_names or [f"feature_{i}" for i in range(n_feat)]

        # kolon sayısı mismatch olursa güvenli düzelt
        if len(cols) != n_feat:
            cols = [f"feature_{i}" for i in range(n_feat)]

        return pd.DataFrame(x, columns=cols)

    def _ensure_model_feature_names(self, model: Any) -> None:
        """
        Sklearn warning fix:
        - Model fit edilirken feature name yoksa ama bizde feature_names varsa,
          sklearn'in beklediği attribute'u ekler.
        Not: Bu sadece warning'i düzeltir; feature sırası/kolon sayısı zaten _to_feature_df ile korunuyor.
        """
        try:
            if model is None or not self.feature_names:
                return

            # sklearn 1.0+ modellerinde fit sonrası bu alan oluşur:
            has = getattr(model, "feature_names_in_", None)
            if has is None:
                # numpy array olması sklearn tarafının beklediği tip
                setattr(model, "feature_names_in_", np.asarray(self.feature_names, dtype=object))
        except (AttributeError, TypeError, ValueError) as e:
            self.logger.debug(f"[FEATURE_NAMES_INJECT_SKIP] {type(model).__name__}: {e}")

    def _detect_any_exchange_from_dir(self) -> Optional[str]:
        """
        models/ içinde 'mexc_gradient_boost_model.pkl' gibi dosyalardan exchange adını yakalamaya çalışır.
        """
        try:
            if not os.path.isdir(self.models_dir):
                return None
            for fn in os.listdir(self.models_dir):
                if not fn.endswith("_model.pkl"):
                    continue
                # ör: mexc_gradient_boost_model.pkl
                parts = fn.split("_")
                if len(parts) >= 3:
                    ex = parts[0].strip().lower()
                    if ex:
                        return ex
        except Exception:
            return None
        return None

    # ---------------------------------------------------------------------
    # Model init
    # ---------------------------------------------------------------------

    def _initialize_models(self) -> None:
        """
        Makine öğrenmesi modellerini başlatır.
        """
        try:
            self.scaler = StandardScaler()

            model_configs = {
                'random_forest': RandomForestClassifier(
                    n_estimators=100,
                    random_state=42,
                    n_jobs=-1
                ),
                'xgboost': xgb.XGBClassifier(
                    random_state=42,
                    n_estimators=100,
                    learning_rate=0.1
                ),
                'lightgbm': lgb.LGBMClassifier(
                    random_state=42,
                    n_estimators=100
                ),
                'gradient_boost': GradientBoostingClassifier(
                    random_state=42,
                    n_estimators=100
                )
            }

            self.models = model_configs
            self.logger.info("✅ Modeller başarıyla başlatıldı")

        except Exception as e:
            self.logger.error(f"❌ Model başlatma hatası: {e}")

    # ---------------------------------------------------------------------
    # Predict
    # ---------------------------------------------------------------------

    def predict_ensemble_safe(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Güvenli ensemble prediction - 3 sınıf (SELL/HOLD/BUY) için.
        """
        try:
            if not self.is_trained:
                self.logger.warning("⚠️ Model eğitilmemiş")
                return None

            if df is None or df.empty or len(df) < 20:
                self.logger.warning("⚠️ Yetersiz veri")
                return None

            features = self.extract_features_from_dataframe(df)

            if features is None or features.size == 0:
                self.logger.warning("⚠️ Özellik çıkarılamadı")
                return None

            if features.ndim > 1:
                last_features = features[-1:].reshape(1, -1)
            else:
                last_features = features.reshape(1, -1)

            if self.scaler is None:
                self.degraded_mode = True
                self.logger.warning("⚠️ Scaler yok (degraded_mode). AI tahmini HOLD döndürüyor.")
                return {
                    'signal':'HOLD',
                    'confidence':0.0,
                    'class_probabilities':[0.0, 1.0, 0.0],
                    'predicted_class':1,
                    'model_count':0,
                    'degraded':True,
                    'reason':'scaler_missing'
                }

            try:
                scaled_features = self.scaler.transform(last_features)
            except Exception as scale_error:
                self.logger.error(f"❌ Scaling hatası: {scale_error}")
                return None

            scaled_df = self._to_feature_df(scaled_features)

            predictions = []
            for name, model in self.models.items():
                if model is None:
                    continue
                try:
                    self._ensure_model_feature_names(model)
                    pred_proba = model.predict_proba(scaled_df)
                    if pred_proba is not None and len(pred_proba) > 0:
                        predictions.append(pred_proba[0])
                except Exception as model_error:
                    self.logger.error(f"❌ {name} model prediction hatası: {model_error}")
                    continue

            if not predictions:
                self.logger.warning("⚠️ Hiçbir model prediction üretemedi")
                return None

            avg_proba = np.mean(predictions, axis=0)
            predicted_class = int(np.argmax(avg_proba))
            confidence = float(avg_proba[predicted_class])

            signal_map = {0: 'SELL', 1: 'HOLD', 2: 'BUY'}
            signal = signal_map.get(predicted_class, 'HOLD')

            return {
                'signal': signal,
                'confidence': confidence,
                'class_probabilities': avg_proba.tolist(),
                'predicted_class': predicted_class,
                'model_count': len(predictions)
            }

        except Exception as e:
            self.logger.error(f"❌ Ensemble prediction hatası: {e}", exc_info=True)
            return None

    def predict(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Güvenli predict metodu (ensemble ortalaması)
        """
        try:
            features = self.extract_features_from_dataframe(df)
            last_features = features[-1:] if len(features) > 0 else features

            if self.scaler is None:
                # Scaler yoksa AI tahminini güvenli şekilde devre dışı bırak
                return {
                    'signal':'HOLD',
                    'confidence':0.0,
                    'class_probabilities':[0.0, 1.0, 0.0],
                    'predicted_class':1,
                    'degraded':True,
                    'reason':'scaler_missing'
                }

            scaled_features = self.scaler.transform(last_features)
            scaled_df = self._to_feature_df(scaled_features)

            predictions = []
            for name, model in self.models.items():
                if model is None:
                    continue
                try:
                    self._ensure_model_feature_names(model)
                    pred_proba = model.predict_proba(scaled_df)
                    predictions.append(pred_proba[0])
                except Exception as model_error:
                    self.logger.error(f"❌ {name} model prediction hatası: {model_error}")

            if not predictions:
                return {'signal': 'HOLD', 'confidence': 0.5}

            avg_proba = np.mean(predictions, axis=0)
            predicted_class = int(np.argmax(avg_proba))
            confidence = float(avg_proba[predicted_class])

            signal_map = {0: 'SELL', 1: 'HOLD', 2: 'BUY'}
            signal = signal_map.get(predicted_class, 'HOLD')

            return {
                'signal': signal,
                'confidence': confidence,
                'class_probabilities': avg_proba.tolist(),
                'predicted_class': predicted_class
            }

        except Exception as e:
            self.logger.error(f"❌ Predict genel hatası: {e}", exc_info=True)
            return {'signal': 'HOLD', 'confidence': 0.5}

    def predict_ensemble(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Ensemble prediction metodu - wrapper
        """
        try:
            return self.predict_ensemble_safe(df)
        except Exception as e:
            self.logger.error(f"❌ Ensemble prediction hatası: {e}", exc_info=True)
            return None

    # ---------------------------------------------------------------------
    # Training
    # ---------------------------------------------------------------------

    def _preprocess_data(self, x_train: np.ndarray, y_train: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Eğitim verilerini ön işleme tabi tutar.
        """
        try:
            mask = ~(np.isnan(x_train).any(axis=1) | np.isinf(x_train).any(axis=1))
            x_train = x_train[mask]
            y_train = y_train[mask]

            if len(x_train) < 50:
                self.logger.warning("⚠️ Çok az eğitim verisi")
                repeat_factor = max(1, int(np.ceil(50 / max(1, len(x_train)))))
                x_train = np.repeat(x_train, repeat_factor, axis=0)
                y_train = np.repeat(y_train, repeat_factor)

            unique, counts = np.unique(y_train, return_counts=True)

            if len(unique) < 2 or np.min(counts) < 5:
                self.logger.warning("⚠️ Dengesiz sınıf dağılımı")
                from sklearn.utils import resample

                balanced_x_train = []
                balanced_y_train = []
                max_count = int(np.max(counts)) if len(counts) else len(y_train)

                for label in unique:
                    label_mask = y_train == label
                    label_x = x_train[label_mask]

                    resampled_x = resample(
                        label_x,
                        replace=True,
                        n_samples=max_count,
                        random_state=42
                    )
                    resampled_y = np.full(resampled_x.shape[0], label)

                    balanced_x_train.append(resampled_x)
                    balanced_y_train.append(resampled_y)

                x_train = np.vstack(balanced_x_train)
                y_train = np.concatenate(balanced_y_train)

            return x_train, y_train

        except Exception as e:
            self.logger.error(f"❌ Veri ön işleme hatası: {e}", exc_info=True)
            return x_train, y_train

    def _train(self, x_train: np.ndarray, y_train: np.ndarray) -> bool:
        """
        Modelleri eğitir.
        """
        try:
            x_train, y_train = self._preprocess_data(x_train, y_train)

            if len(x_train) < 10:
                self.logger.error("❌ Yetersiz eğitim verisi")
                return False

            x_train, x_val, y_train, y_val = train_test_split(
                x_train, y_train, test_size=0.2, random_state=42, stratify=y_train
            )

            feature_names = [f'feature_{i}' for i in range(x_train.shape[1])]
            self.feature_names = feature_names

            # Scaler fit
            if self.scaler is None:
                self.scaler = StandardScaler()
            self.scaler.fit(x_train)

            x_train_scaled = self.scaler.transform(x_train)
            x_val_scaled = self.scaler.transform(x_val)

            x_train_scaled_df = pd.DataFrame(x_train_scaled, columns=feature_names)
            x_val_scaled_df = pd.DataFrame(x_val_scaled, columns=feature_names)

            model_results = {}
            for name, model in self.models.items():
                try:
                    if model is None:
                        continue

                    model.fit(x_train_scaled_df, y_train)

                    y_pred = model.predict(x_val_scaled_df)
                    accuracy = accuracy_score(y_val, y_pred)

                    model_results[name] = {
                        'accuracy': float(accuracy),
                        'report': classification_report(y_val, y_pred)
                    }

                    self.logger.info(f"✅ {name} modeli eğitildi (Accuracy: {accuracy})")

                except Exception as model_error:
                    self.logger.error(f"❌ {name} eğitim hatası: {model_error}", exc_info=True)

            # Eğitim metadata (feature_names dahil!)
            self.training_metadata = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'model_results': model_results,
                'feature_names': feature_names
            }

            self.is_trained = True
            return True

        except Exception as e:
            self.logger.error(f"❌ Genel eğitim hatası: {e}", exc_info=True)
            return False

    def train(self, training_data: List[Tuple[str, pd.DataFrame]], exchange: str) -> bool:
        """
        Eğitim metodunu güvenli hale getir
        """
        try:
            valid_data = self.prepare_training_data(training_data)

            if len(valid_data) < 10:
                self.logger.error("❌ Eğitim için yeterli veri yok")
                return False

            features = []
            labels = []

            for symbol, dataframe in valid_data:
                symbol_features = self.extract_features_from_dataframe(dataframe, symbol)
                symbol_labels = self.create_target_labels_for_symbol(dataframe)

                min_len = min(symbol_features.shape[0], len(symbol_labels))
                if min_len <= 0:
                    continue

                features.append(symbol_features[:min_len])
                labels.extend(symbol_labels[:min_len])

            if not features or len(labels) < 10:
                self.logger.error("❌ Eğitim için birleştirilebilir veri yok")
                return False

            x_train = np.vstack(features)
            y_train = np.array(labels, dtype=int)

            training_result = self._train(x_train, y_train)

            if training_result:
                self.save_models(exchange=exchange)

            return training_result

        except Exception as e:
            self.logger.error(f"❌ Eğitim genel hatası: {e}", exc_info=True)
            return False

    # ---------------------------------------------------------------------
    # Data prep / labels
    # ---------------------------------------------------------------------

    @staticmethod
    def debug_training_data(training_data):
        print("🔍 Eğitim Verisi Detayları:")
        for symbol, dataframe in training_data:
            print(f"Sembol: {symbol}")
            print(f"Toplam satır sayısı: {len(dataframe)}")
            print(f"NaN sayısı:\n{dataframe.isna().sum()}")
            print(f"Sütunlar: {dataframe.columns}")
            print(f"İlk 5 satır:\n{dataframe.head()}")
            print("-" * 50)

    @staticmethod
    def prepare_training_data(training_data: List[Tuple[str, pd.DataFrame]]) -> List[Tuple[str, pd.DataFrame]]:
        """
        Eğitim verilerini hazırlar ve filtreler.
        """
        valid_data = []
        for symbol, dataframe in training_data:
            try:
                dataframe = dataframe.dropna()
                if len(dataframe) >= 50:
                    valid_data.append((symbol, dataframe))
            except Exception:
                continue
        return valid_data

    def create_target_labels_for_symbol(self, dataframe: pd.DataFrame) -> List[int]:
        """
        Tek bir sembol için hedef etiketleri oluşturur: 0=SELL, 1=HOLD, 2=BUY
        """
        try:
            df = dataframe.dropna()

            if len(df) < 20:
                self.logger.warning("⚠️ Yetersiz veri için varsayılan etiketler")
                return [1] * len(df)

            price_changes = df['close'].pct_change().dropna()

            labels = []
            for change in price_changes:
                if change > 0.01:
                    labels.append(2)
                elif change < -0.01:
                    labels.append(0)
                else:
                    labels.append(1)

            labels = [1] + labels  # ilk NaN için
            return labels

        except Exception as e:
            self.logger.error(f"❌ Etiket oluşturma hatası: {e}", exc_info=True)
            return [1] * len(dataframe)

    @staticmethod
    def create_target_labels(valid_data: List[Tuple[str, pd.DataFrame]]) -> np.ndarray:
        """
        Toplu etiket üretimi (kullanılmıyor olabilir; korundu)
        """
        labels = []
        for symbol, df in valid_data:
            df = df.dropna()
            price_changes = df['close'].pct_change().dropna()
            symbol_labels = [
                2 if change > 0.01 else
                0 if change < -0.01 else
                1
                for change in price_changes
            ]
            labels.extend(symbol_labels)

        if len(labels) < 100:
            additional_labels = [
                2 if i % 3 == 0 else
                0 if i % 3 == 1 else
                1
                for i in range(100 - len(labels))
            ]
            labels.extend(additional_labels)

        return np.array(labels, dtype=int)

    # ---------------------------------------------------------------------
    # Save / Load (exchange aware)
    # ---------------------------------------------------------------------

    def save_models(self, exchange: str) -> bool:
        """
        Modelleri ve scaler'ı exchange prefix ile kaydeder.
        metadata.json içine exchange bazlı metadata yazar (feature_names dahil).
        """
        try:
            ex = self._exchange_norm(exchange)
            if not ex:
                self.logger.error("❌ exchange boş; model kaydedilemez")
                return False

            # Model kaydetme
            for name, model in self.models.items():
                if model is None:
                    continue
                model_path = os.path.join(self.models_dir, f'{ex}_{name}_model.pkl')
                joblib.dump(model, model_path)

            # Scaler kaydetme
            if self.scaler is not None:
                scaler_path = os.path.join(self.models_dir, f'{ex}_scaler.pkl')
                joblib.dump(self.scaler, scaler_path)

            # Metadata kaydetme (merge)
            all_metadata = self._read_metadata_file()

            # training_metadata içine feature_names koy (garanti)
            if not isinstance(self.training_metadata, dict):
                self.training_metadata = {}
            if self.feature_names and "feature_names" not in self.training_metadata:
                self.training_metadata["feature_names"] = self.feature_names

            all_metadata[ex] = self.training_metadata

            self._write_metadata_file(all_metadata)

            self.logger.info(f"✅ {ex.upper()} için modeller başarıyla kaydedildi")
            return True

        except Exception as e:
            self.logger.error(f"❌ Model kaydetme hatası: {e}", exc_info=True)
            return False

    def load_models_for_exchange(self, exchange: str) -> bool:
        """
        Belirli bir borsa için eğitilmiş modelleri yükler.
        Dosya adları: '{exchange}_{modelname}_model.pkl' ve '{exchange}_scaler.pkl'
        """
        try:
            ex = self._exchange_norm(exchange)
            if not ex:
                self.logger.error("❌ Borsa adı belirtilmedi, model yüklenemiyor.")
                return False

            self.logger.info(f"[{ex.upper()}] için özel modeller yükleniyor...")

            # Feature names yükle (varsa)
            ex_meta = self._load_exchange_metadata(ex)
            fns = ex_meta.get("feature_names")
            if isinstance(fns, list) and fns:
                self.feature_names = [str(x) for x in fns]
            else:
                self.feature_names = None

            # Modelleri yükle
            loaded_any = 0
            for name in ['random_forest', 'xgboost', 'lightgbm', 'gradient_boost']:
                model_path = os.path.join(self.models_dir, f'{ex}_{name}_model.pkl')
                if os.path.exists(model_path):
                    try:
                        self.models[name] = joblib.load(model_path)
                        loaded_any += 1
                    except Exception as load_error:
                        self.logger.error(f"❌ {ex.upper()} {name} model yükleme hatası: {load_error}", exc_info=True)
                        self.models[name] = None
                else:
                    self.models[name] = None

            # Scaler yükle
            scaler_path = os.path.join(self.models_dir, f'{ex}_scaler.pkl')
            if os.path.exists(scaler_path):
                try:
                    self.scaler = joblib.load(scaler_path)
                except Exception as scaler_error:
                    self.logger.error(f"❌ {ex.upper()} scaler yükleme hatası: {scaler_error}", exc_info=True)
                    self.scaler = None
            else:
                self.scaler = None

            self.degraded_mode = (self.scaler is None)
            self.is_trained = (loaded_any > 0)

            self.logger.info(
                f"✅ {ex.upper()} için modeller yüklendi - Aktif model: {loaded_any}, "
                f"Scaler: {'Var' if self.scaler else 'Yok'}, "
                f"FeatureNames: {'Var' if self.feature_names else 'Yok'}"
            )
            return self.is_trained

        except Exception as e:
            self.logger.error(f"❌ {exchange} için model yükleme hatası: {e}", exc_info=True)
            return False

    def load_models(self) -> bool:
        """
        Legacy yükleyici:
        1) Önce exchange'siz dosyaları dener (random_forest_model.pkl, scaler.pkl).
        2) Bulamazsa metadata.json'dan veya klasörden bir exchange tespit edip
           load_models_for_exchange() ile yüklemeyi dener.
        """
        try:
            # 1) Legacy (exchange'siz) yükleme dene
            any_loaded = 0
            for name in ['random_forest', 'xgboost', 'lightgbm', 'gradient_boost']:
                model_path = os.path.join(self.models_dir, f'{name}_model.pkl')
                if os.path.exists(model_path):
                    try:
                        self.models[name] = joblib.load(model_path)
                        any_loaded += 1
                    except Exception as load_error:
                        self.logger.error(f"❌ {name} model yükleme hatası: {load_error}", exc_info=True)
                        self.models[name] = None
                else:
                    self.models[name] = None

            scaler_path = os.path.join(self.models_dir, 'scaler.pkl')
            if os.path.exists(scaler_path):
                try:
                    self.scaler = joblib.load(scaler_path)
                except Exception as scaler_error:
                    self.logger.error(f"❌ Scaler yükleme hatası: {scaler_error}", exc_info=True)
                    self.scaler = None
            else:
                self.scaler = None
            # Legacy modeller için feature_names yüklemeyi dene (metadata.json'dan)
            try:
                all_meta = self._read_metadata_file()
                if isinstance(all_meta, dict) and all_meta and not self.feature_names:
                    best_ex = None
                    best_ts = None
                    for ex, meta in all_meta.items():
                        if not isinstance(meta, dict):
                            continue
                        ts = meta.get("timestamp")
                        if not isinstance(ts, str) or not ts:
                            continue
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if best_ts is None or dt > best_ts:
                            best_ts = dt
                            best_ex = ex

                    if best_ex:
                        fns = (all_meta.get(best_ex) or {}).get("feature_names")
                        if isinstance(fns, list) and fns:
                            self.feature_names = [str(c) for c in fns]
                    for model in self.models.values():
                        self._ensure_model_feature_names(model)

            except (OSError, ValueError, TypeError) as e:
                self.logger.debug(f"[LEGACY_FEATURE_NAMES_LOAD_SKIP] {e}")

            if any_loaded > 0 and self.scaler is not None:
                self.is_trained = True
                self.logger.info(f"✅ Legacy modeller yüklendi - Aktif model sayısı: {any_loaded}")
                return True

            # 2) Exchange tespit edip exchange'li yükleme dene
            all_meta = self._read_metadata_file()
            ex_guess = None
            if isinstance(all_meta, dict) and all_meta:
                # en güncel timestamp'e sahip exchange'i seç
                best_ex = None
                best_ts = None
                for ex, meta in all_meta.items():
                    if not isinstance(meta, dict):
                        continue
                    ts = meta.get("timestamp")
                    if not isinstance(ts, str) or not ts:
                        continue
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if best_ts is None or dt > best_ts:
                        best_ts = dt
                        best_ex = ex
                ex_guess = best_ex

            if not ex_guess:
                ex_guess = self._detect_any_exchange_from_dir()

            if ex_guess:
                return self.load_models_for_exchange(ex_guess)

            self.is_trained = False
            self.logger.warning("⚠️ Hiçbir model yüklenemedi (legacy + exchange fallback başarısız)")
            return False

        except Exception as e:
            self.logger.error(f"❌ Model yükleme genel hatası: {e}", exc_info=True)
            return False

    # ---------------------------------------------------------------------
    # Feature extraction
    # ---------------------------------------------------------------------

    def extract_features_from_dataframe(self, dataframe: pd.DataFrame, symbol: str = None) -> np.ndarray:
        """
        DataFrame'den her satır için özellik çıkarır.
        Çıktı: (n_rows, 12) numpy array
        """
        try:
            required_columns = ['close', 'volume']
            for col in required_columns:
                if col not in dataframe.columns:
                    self.logger.warning(f"⚠️ Eksik sütun: {col} - {symbol}")
                    return np.zeros((len(dataframe), 12))

            if len(dataframe) < 20:
                self.logger.warning(f"⚠️ Yetersiz veri: {symbol}")
                return np.zeros((len(dataframe), 12))

            if not self.feature_names:
                self.feature_names = [f"feature_{i}" for i in range(12)]

            dataframe = dataframe.dropna()
            if len(dataframe) == 0:
                return np.zeros((1, 12))

            features_list = []
            for i in range(len(dataframe)):
                current_data = dataframe.iloc[:i + 1] if i > 0 else dataframe.iloc[:1]

                closes = current_data['close'].values
                volumes = current_data['volume'].values

                features = [
                    float(closes[-1]) if len(closes) > 0 else 0.0,
                    float(np.mean(volumes[-min(10, len(volumes)):])) if len(volumes) > 0 else 0.0,
                    float(np.mean(closes[-min(5, len(closes)):])) if len(closes) > 0 else 0.0,
                    float(np.mean(closes[-min(20, len(closes)):])) if len(closes) > 0 else 0.0,
                    float(np.max(closes)) if len(closes) > 0 else 0.0,
                    float(np.min(closes)) if len(closes) > 0 else 0.0,
                    float(closes[0]) if len(closes) > 0 else 0.0,
                    float(np.std(closes)) if len(closes) > 1 else 0.0,
                    float(np.mean(closes)) if len(closes) > 0 else 0.0,
                    float(np.percentile(closes, 25)) if len(closes) > 0 else 0.0,
                    float(np.percentile(closes, 75)) if len(closes) > 0 else 0.0,
                    float(np.mean(np.diff(closes))) if len(closes) > 1 else 0.0,
                ]

                features = [f if (not np.isnan(f) and not np.isinf(f)) else 0.0 for f in features]
                features_list.append(features)

            return np.array(features_list, dtype=np.float64)

        except Exception as e:
            self.logger.error(f"❌ Özellik çıkarma hatası: {symbol} - {e}", exc_info=True)
            n = len(dataframe) if dataframe is not None and len(dataframe) > 0 else 1
            return np.zeros((n, 12))

    # ---------------------------------------------------------------------
    # Info
    # ---------------------------------------------------------------------

    def get_model_info(self) -> Dict[str, Any]:
        return {
            'is_trained': self.is_trained,
            'model_count': len(self.models),
            'models': list(self.models.keys()),
            'training_timestamp': self.training_metadata.get('timestamp', 'Eğitilmemiş'),
            'model_results': self.training_metadata.get('model_results', {}),
            'feature_names': self.feature_names or []
        }
