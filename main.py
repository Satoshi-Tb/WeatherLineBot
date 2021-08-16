# -*- coding: utf-8 -*-

#  Licensed under the Apache License, Version 2.0 (the "License"); you may
#  not use this file except in compliance with the License. You may obtain
#  a copy of the License at
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#  WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#  License for the specific language governing permissions and limitations
#  under the License.

from __future__ import unicode_literals

import os
import sys
import requests
import urllib
from lxml import etree
from flask import Flask, request, abort
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, LocationMessage, CarouselColumn, TemplateSendMessage, CarouselTemplate,
    ButtonsTemplate, MessageAction,
)
import muni

app = Flask(__name__)

# get channel_secret and channel_access_token from your environment variable
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)

if channel_secret is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)
handler = WebhookHandler(channel_secret)


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['x-line-signature']

    # get request body as text
    body = request.get_data(as_text=True)

    # POSTデータをherokuログに出力
    print("Request body: " + body)

    # parse webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print(InvalidSignatureError)
        abort(400)

    return 'OK'


# テキストメッセージハンドラ
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    ng_message = "天気予報が取得できませんでした(;><)"
    print("callback start")
    try:
        # 検索結果候補
        geo_info = get_geo_info_from_text(event.message.text)
        if len(geo_info) == 1:
            lon, lat = geo_info[0]["geometry"]["coordinates"]
            weather_data = get_weather_from_geocode(lat, lon)
            if len(weather_data) == 0:
                message = ng_message
            else:
                message = create_message_from_weather_data(weather_data)

            messages = TextSendMessage(text=message)

        elif len(geo_info) == 0:
            message = "該当する住所が見つかりません！\n検索キーワードを見直してください"
            messages = TextSendMessage(text=message)

        elif len(geo_info) > 5:
            message = "検索結果が多すぎます。。。\n検索キーワードを見直してください"
            messages = TextSendMessage(text=message)

        else:
            actions = []
            for geo in geo_info:
                actions.append(MessageAction(
                    label=geo["properties"]["title"],
                    text=geo["properties"]["title"]
                ))
            messages = TemplateSendMessage(
                alt_text='template',
                template=ButtonsTemplate(
                    title="位置情報の選択",
                    text="候補を選択してください",
                    actions=actions
                ),
            )

    except Exception as e:
        print(f"handle_message error\n{e}")
        messages = TextSendMessage(text=ng_message)

    line_bot_api.reply_message(
        event.reply_token,
        messages=messages)


#
def get_geo_info_from_text(address_text):
    try:
        quoted = urllib.parse.quote(address_text)
        request_uri = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={quoted}"
        resp_data = requests.get(request_uri)
        if resp_data.status_code != 200:
            print(f"get_weather_from_text error: Weather area data request error. \nURI={request_uri}\nstatus code={resp_data.status_code}")
            return {}

        return resp_data.json()
    except Exception as e:
        print(f"get_weather_from_text error: '{e}'")
        return {}


# ロケーションメッセージハンドラ
@handler.add(MessageEvent, message=LocationMessage)
def handle_image_message(event):
    ng_message = "天気予報が取得できませんでした(;><)"
    try:
        weather_data = get_weather_from_geocode(event.message.latitude, event.message.longitude)
        if len(weather_data) == 0:
            message = ng_message
        else:
            message = create_message_from_weather_data(weather_data)

    except Exception as e:
        print(f"handle_image_message error\n{e}")
        message = ng_message

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=message))


def create_message_from_weather_data(weather_data):
    return f"{weather_data['title']}\r\n{weather_data['forecasts'][0]['date']} : {weather_data['forecasts'][0]['telop']}\r\n{weather_data['description']['headlineText']}"


# 国土地理院リバースジオコーダーAPIを利用し、経度、緯度情報から県名を取得
def reverse_geocode(lat, lon):
    try:
        req_uri = f"https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={lat}&lon={lon}"
        resp_rev_geo = requests.get(req_uri)

        if resp_rev_geo.status_code != 200:
            print(f"reverse_geocode error\nrequest uri = {req_uri}\nstatus_code={resp_rev_geo.status_code}")
            return "", ""

        rev_geo_data = resp_rev_geo.json()
        if len(rev_geo_data) == 0:
            print(f"reverse_geocode error. Invalid get data. latitude = '{lat}', longitude = '{lon}'")
            return "", ""

        muni_cd = rev_geo_data['results']['muniCd']
        muni_cd = str(int(muni_cd))  # 先頭の0をカット

        if muni_cd not in muni.MUNI:
            print(f"reverse_geocode error: Invalid muni cd '{muni_cd}'")
            return "", ""

        location_info = muni.MUNI[muni_cd].split(",")
        return location_info[1], location_info[3]

    except Exception as e:
        print(f"reverse_geocode error\n{e}")
        return "", ""


# 天気予報 API（livedoor 天気互換）を利用して、県名・市名から天気予報情報を取得
def get_weather_from_geocode(lat, lon):
    try:
        prefecture, city = reverse_geocode(lat, lon)
        if prefecture == "":
            return {}

        # 天気予報API用の都市コード取得
        req_uri = "https://weather.tsukumijima.net/primary_area.xml"
        resp_area_data = requests.get(req_uri)
        if resp_area_data.status_code != 200:
            print(f"get_weather_from_geocode: Weather area data request error. \nURI={req_uri}\nstatus code={resp_area_data.status_code}")
            return {}

        bytes_data = bytes(bytearray(resp_area_data.text, encoding='utf-8'))
        xml_obj = etree.XML(bytes_data)

        if prefecture == "北海道":
            city_list = xml_obj.xpath(".//pref[contains(@title, '道')]/city")
        else:
            city_list = xml_obj.xpath(f".//pref[@title='{prefecture}']/city")

        if len(city_list) == 0:
            # 都市情報取得失敗
            print(f"get_weather_from_geocode: The specified prefecture name '{prefecture}' is invalid. ")
            return {}

        # 都市コード取得
        city_code = ""
        for c in city_list:
            if city_code == "":
                # 都市名が1件もヒットしない場合、最初の都市の予報情報を表示
                # TODO 市名から候補指定してもらうのもあり
                city_code = c.get("id")

            if c.get("title") in city:
                city_code = c.get("id")
                break

        # 天気予報APIリクエスト
        req_uri = f"https://weather.tsukumijima.net/api/forecast?city={city_code}"
        resp_weather = requests.get(req_uri)
        if resp_weather.status_code != 200:
            print(f"get_weather_from_geocode: Weather API call error. \nURI={req_uri}\nstatus code={resp_weather.status_code}")
            return {}

        weather_data = resp_weather.json()

        if "error" in weather_data:
            print(f"get_weather_from_geocode: The specified city ID '{city_code}' is invalid. Error Message:'{weather_data['error']}'")
            return {}

        return weather_data

    except Exception as e:
        print(f"get_weather_from_geocode: error\n{e}")
        return {}


if __name__ == "__main__":
    #    app.run()
    port = int(os.getenv("PORT"))
    app.run(host="0.0.0.0", port=port)
