import argparse
import os
from datetime import date
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP

from .data import TravelStore
from .settings import db_path


def build_server(port=8001, path=None):
    store = TravelStore(path or db_path())
    server = FastMCP('TripWeaveTravelTools', host='127.0.0.1', port=port,
                     stateless_http=True, json_response=True, log_level='WARNING')

    @server.tool()
    async def query_weather(city: str, travel_date: str) -> dict:
        """按城市和 YYYY-MM-DD 日期查询天气。提供者由服务端配置，不接受 SQL。"""
        day = date.fromisoformat(travel_date)
        provider = os.getenv('TRIPWEAVE_WEATHER_PROVIDER', 'sample')
        if provider == 'sample':
            if city not in ('北京', '上海') or travel_date != '2026-10-01':
                return {'status': 'no_data', 'message': '样例仅含北京/上海 2026-10-01；实时预报请配置 open_meteo'}
            return {'status': 'success', 'city': city, 'date': travel_date, 'temperature_max': 24,
                    'temperature_min': 16, 'source': '固定演示天气，不是真实预报'}
        if provider != 'open_meteo':
            raise ValueError('未知天气数据提供者')
        if not 0 <= (day - date.today()).days <= 15:
            return {'status': 'no_data', 'message': '仅查询今天起16天内预报；日期超出范围'}
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            geo = await client.get('https://geocoding-api.open-meteo.com/v1/search',
                                   params={'name': city, 'count': 2, 'language': 'zh', 'countryCode': 'CN'})
            geo.raise_for_status()
            places = geo.json().get('results', [])
            if not places:
                return {'status': 'no_data', 'message': '未定位到城市，请提供更明确的城市名称'}
            if len(places) > 1:
                return {'status': 'no_data', 'message': '城市定位存在多个候选，请补充城市信息',
                        'candidates': [p.get('name') for p in places]}
            place = places[0]
            response = await client.get('https://api.open-meteo.com/v1/forecast', params={
                'latitude': place['latitude'], 'longitude': place['longitude'], 'start_date': travel_date,
                'end_date': travel_date, 'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum',
                'timezone': 'Asia/Shanghai'})
            response.raise_for_status()
            return {'status': 'success', 'city': place['name'], 'coordinates': [place['latitude'], place['longitude']],
                    'data': response.json().get('daily', {}), 'source': 'https://open-meteo.com/'}

    @server.tool()
    def query_tickets(kind: Literal['train', 'flight', 'concert'], departure_city: str,
                      arrival_city: str, travel_date: str) -> dict:
        """参数化查询演示票务。日期YYYY-MM-DD，不接收SQL；concert出发与到达均填举办城市。"""
        date.fromisoformat(travel_date)
        return store.tickets(kind, departure_city, arrival_city, travel_date)

    @server.tool()
    def book_simulated_ticket(ticket_id: str, quantity: int, request_id: str) -> dict:
        """仅创建本地模拟订单；request_id由执行器注入，支持同请求重放，无真实出票。"""
        if not request_id or len(request_id) > 128:
            raise ValueError('请求ID不合法')
        return store.book(ticket_id, quantity, request_id)

    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    build_server(args.port).run(transport='streamable-http')
