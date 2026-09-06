
# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---
from flask import Flask, request

import json
import re
import threading

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import settings

app = Flask(__name__)
UD_type=["u","U","d","D"]
LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_ACCESS_TOKEN
LINE_CHANNEL_SECRET = settings.LINE_CHANNEL_SECRET

@app.route("/", methods=['POST'])
def linebot():
    body = request.get_data(as_text=True)                    
    groupID=""
    reply=""
    try:
        json_data = json.loads(body)                         
        line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)              
        #-----安全認證（secret 改由 .env 提供，不再寫死在程式碼裡）
        handler = WebhookHandler(LINE_CHANNEL_SECRET)
        signature = request.headers['X-Line-Signature']
        handler.handle(body, signature)
        #------
        tk = json_data['events'][0]['replyToken']            
        type = json_data['events'][0]['message']['type']     
        UserID = json_data['events'][0]["source"]["userId"]
        timestamp = json_data['events'][0]["timestamp"]
        type = json_data['events'][0]["source"]["type"]
        
        if type == "group":
            print("groupID:{0}".format(json_data['events'][0]["source"]["groupId"]))
        else:
            print("UserID:{0}".format(json_data['events'][0]["source"]["userId"]))
        if type=='text':
            msg = json_data['events'][0]['message']['text']  
            # print(json_data)
            print(msg)
    except Exception as e:
        print(e)                                          
        # reply = str(e)
        # line_bot_api.reply_message(tk,TextSendMessage(reply))# 回傳訊息
    return 'OK'                                              

if __name__ == "__main__":
    missing = [k for k in ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET")
               if not getattr(settings, k)]
    if missing:
        raise SystemExit(
            "缺少必要設定: " + ", ".join(missing) + "\n請在 .env 補上，格式見 .env.example"
        )
    app.run()