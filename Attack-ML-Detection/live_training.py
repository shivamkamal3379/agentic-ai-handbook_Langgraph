import json
import xgboost as xgb
import pandas as pd
import numpy as np
from datetime import datetime
import os
import shutil


class LiveTrainingPipeline:
    def __init__(
        self,
        training_data_json="training_data.json",
        model_path="xgboost_model_binary.json",
        retrain_threshold=10,
    ):

        self.training_data_json = training_data_json
        self.model_path = model_path
        self.retrain_threshold = retrain_threshold
        self.new_samples_count = 0

    def initialize_from_csv(self, csv_path):
        """Converts initial CSV dataset to JSON format if JSON doesn't exist."""
        if os.path.exists(self.training_data_json):
            print(f"Training data JSON already exists at {self.training_data_json}")
            return

        try:
            df = pd.read_csv(csv_path)
            # Assuming last column is target, but let's try to be smart or generic
            # Based on attack_detection.py features, we should ensure we save correct columns

            # Construct JSON structure
            training_data = {
                "metadata": {
                    "created_at": datetime.now().isoformat(),
                    "total_samples": len(df),
                    "version": 1,
                },
                "data": [],
            }

            for _, row in df.iterrows():
                # We need to ensure we only save the features used by the model
                # For now, we save everything and filter later or assume CSV is pre-processed
                record = {
                    "features": row.iloc[:-1].tolist(),  # All cols except last
                    "target": int(row.iloc[-1]),  # Last col is target
                    "source": "initial_csv",
                    "added_at": datetime.now().isoformat(),
                }
                training_data["data"].append(record)

            with open(self.training_data_json, "w") as f:
                json.dump(training_data, f, indent=2)

            print(f"Initialized training data from {csv_path} with {len(df)} samples.")

        except Exception as e:
            print(f"Error initializing from CSV: {e}")

    def load_training_data(self):
        if not os.path.exists(self.training_data_json):
            return {"data": [], "metadata": {"version": 0, "total_samples": 0}}

        with open(self.training_data_json, "r") as f:
            return json.load(f)

    def append_correction(self, features, label):
        """
        Appends a corrected sample to the training data.
        """
        training_data = self.load_training_data()

        new_record = {
            "features": features,
            "target": int(label),
            "source": "user_correction",
            "added_at": datetime.now().isoformat(),
        }

        training_data["data"].append(new_record)
        training_data["metadata"]["total_samples"] += 1
        training_data["metadata"]["last_updated"] = datetime.now().isoformat()

        with open(self.training_data_json, "w") as f:
            json.dump(training_data, f, indent=2)

        self.new_samples_count += 1
        print(
            f"Added correction. New samples pending retrain: {self.new_samples_count}/{self.retrain_threshold}"
        )

        if self.new_samples_count >= self.retrain_threshold:
            self.retrain_model()
            self.new_samples_count = 0

    def retrain_model(self):
        """Retrains the XGBoost model using all data in JSON."""
        print("🚀 Starting model retraining...")

        training_data = self.load_training_data()
        if not training_data["data"]:
            print("No training data found.")
            return

        # Prepare data for XGBoost
        X = []
        y = []
        for record in training_data["data"]:
            X.append(record["features"])
            y.append(record["target"])

        X = np.array(X)
        y = np.array(y)

        # Train XGBoost
        # Note: We use XGBClassifier for training, then save the booster
        model = xgb.XGBClassifier(use_label_encoder=False, eval_metric="logloss")
        model.fit(X, y)

        # Backup old model
        if os.path.exists(self.model_path):
            backup_path = (
                f"{self.model_path}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy(self.model_path, backup_path)
            print(f"Backed up old model to {backup_path}")

        # Save new model
        # vital: save the booster part specifically to match attack_detection.py's load_model logic if needed
        # But attack_detection.py uses xgb.Booster().load_model()
        # So we should save it such that it's compatible.
        model.get_booster().save_model(self.model_path)

        print(f"✅ Model retrained and saved to {self.model_path}")

        # Update metadata version
        training_data["metadata"]["version"] += 1
        with open(self.training_data_json, "w") as f:
            json.dump(training_data, f, indent=2)
