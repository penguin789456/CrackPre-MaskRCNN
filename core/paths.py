"""專案路徑的唯一真相來源。

整理資料夾之後，「raw/」「output/」這類相對路徑會隨呼叫端的工作目錄而變，
從 tools/ 或 legacy/ 執行就會找錯地方。所有位置一律從這裡取絕對路徑。

    PROJECT_ROOT/
    ├── core/        本模組所在
    ├── models/      模型權重與匯出產物
    ├── data/raw/    影像資料
    ├── tools/       命令列工具
    └── legacy/      舊入口腳本
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

CORE_DIR = HERE
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DATA_RAW = os.path.join(DATA_DIR, "raw")
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
LEGACY_DIR = os.path.join(PROJECT_ROOT, "legacy")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
BIN_DIR = os.path.join(PROJECT_ROOT, "bin")

ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
LEGACY_CONFIG = os.path.join(LEGACY_DIR, "config.ini")

MODEL_WEIGHTS = os.path.join(MODELS_DIR, "model_final.pth")
MODEL_TORCHSCRIPT = os.path.join(MODELS_DIR, "model.ts")
MODEL_ONNX = os.path.join(MODELS_DIR, "model.onnx")
MODEL_MANIFEST = os.path.join(MODELS_DIR, "model_manifest.json")


def raw_subdir(name: str) -> str:
    """取得（並建立）data/raw 底下的子資料夾，例如 raw_subdir("RTSP")。"""
    path = os.path.join(DATA_RAW, name)
    os.makedirs(path, exist_ok=True)
    return path
