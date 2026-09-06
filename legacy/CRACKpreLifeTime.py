
# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---
import torch
from detectron2 import config
from detectron2.engine import DefaultPredictor
from detectron2.utils.visualizer import Visualizer
from detectron2.data import MetadataCatalog
import cv2
import matplotlib.pyplot as plt
from detectron2 import model_zoo
import numpy as np
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText
import os
from linebot import LineBotApi
from linebot.models import ImageSendMessage
from linebot.models import TextSendMessage
import pyimgur
import configparser
import paths

configDocument = configparser.ConfigParser()
configDocument.read(paths.LEGACY_CONFIG)

def PreImg(ImgArray):
    try:
        classes = ['diagonal_crack', 'horizontal_crack','vertical_crack']
        cfg = config.get_cfg()
        cfg.merge_from_file(model_zoo.get_config_file(
        "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"))
        cfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url(
        "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = 3 
        cfg.MODEL.WEIGHTS = paths.MODEL_WEIGHTS
        # 降低檢測閾值，使模型能夠檢測到更多可能的裂縫
        cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.5
        cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
        predictor = DefaultPredictor(cfg)
        
        # Process the input image array
        outputs = predictor(ImgArray)
        instances = outputs["instances"]
        
        # Set up metadata for visualization
        MetadataCatalog.get("my_dataset").thing_classes = classes
        metadata = MetadataCatalog.get("my_dataset")

        # Create visualizer and draw predictions
        v = Visualizer(ImgArray[:, :, ::-1], metadata=metadata)
        v = v.draw_instance_predictions(instances.to("cpu"))
        
        # Get the processed image array
        processed_img = v.get_image()[:, :, ::-1]  # Convert back to BGR
        
        return processed_img
        
    except Exception as e:
        print(f"Error in PreImg: {e}")
        import traceback
        traceback.print_exc()
        return ImgArray  # Return original image if processing fails
    