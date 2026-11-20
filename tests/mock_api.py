#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 mock：模拟 DeepSeek Search / Fetch / Chat Completions，用于离线端到端测试。

用法：
    python mock_api.py            # 监听 127.0.0.1:8765
    python collect.py --base-url http://127.0.0.1:8765 --api-key sk-test ...
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

LIST_PAGE = """# 2026 年度浙江省注塑模具行业专精特新名单公示

一、宁波华模精密模具有限公司（统一社会信用代码 91330200TEST00001X，成立 2010-05-12，
注册资本 2000 万人民币，存续，主营精密注塑模具制造）

二、台州精工模具有限公司（统一社会信用代码 91331000TEST00002X，成立 2014-08-01，
注册资本 800 万人民币，存续，主营汽车注塑模具）

三、杭州云模科技有限公司（统一社会信用代码 91330100TEST00003X，成立 2018-03-20，
注册资本 500 万人民币，存续，经营范围含模具设计、注塑加工）
"""

COMPANY_PAGE = """# 宁波华模精密模具有限公司

宁波华模精密模具有限公司成立于 2010 年，位于宁波市北仑区，主营精密注塑模具。
统一社会信用代码：91330200TEST00001X
联系电话：0574-88886666  业务手机：13800138000（本行用于验证手机号过滤）
官网：https://huamo.example.com
"""


def companies_for(user_text: str):
    if "list1" in user_text:
        return [
            {"企业名称": "宁波华模精密模具有限公司", "统一社会信用代码": "91330200TEST00001X",
             "省": "浙江省", "市": "宁波市", "区县": "北仑区", "行业": "专用设备制造业",
             "注册资本": "2000万人民币", "成立日期": "2010-05-12", "经营状态": "存续",
             "经营范围": "精密注塑模具制造", "主营产品": "精密注塑模具",
             "产品介绍": "专业生产精密注塑模具，服务汽车与家电行业，具备 500T~2500T 注塑能力。",
             "官网": "https://huamo.example.com", "公开电话": "0574-88886666",
             "evidence": "宁波华模精密模具有限公司（统一社会信用代码 91330200TEST00001X）",
             "confidence": 0.9},
            {"企业名称": "台州精工模具有限公司", "统一社会信用代码": "91331000TEST00002X",
             "省": "浙江省", "市": "台州市", "区县": "", "行业": "专用设备制造业",
             "注册资本": "800万人民币", "成立日期": "2014-08-01", "经营状态": "存续",
             "经营范围": "汽车注塑模具", "主营产品": "汽车注塑模具",
             "官网": "", "公开电话": "",
             "evidence": "台州精工模具有限公司（统一社会信用代码 91331000TEST00002X）",
             "confidence": 0.9},
            {"企业名称": "杭州云模科技有限公司", "统一社会信用代码": "91330100TEST00003X",
             "省": "浙江省", "市": "杭州市", "区县": "", "行业": "软件和信息技术服务业",
             "注册资本": "500万人民币", "成立日期": "2018-03-20", "经营状态": "存续",
             "经营范围": "模具设计、注塑加工", "主营产品": "模具设计",
             "官网": "", "公开电话": "",
             "evidence": "杭州云模科技有限公司（统一社会信用代码 91330100TEST00003X）",
             "confidence": 0.9},
        ]
    # company 页：与 list 页第一条重复，用于验证去重；公开电话故意给手机号
    return [
        {"企业名称": "宁波华模精密模具有限公司", "统一社会信用代码": "91330200TEST00001X",
         "省": "浙江省", "市": "宁波市", "区县": "北仑区", "行业": "专用设备制造业",
         "注册资本": "2000万人民币", "成立日期": "2010-05-12", "经营状态": "存续",
         "经营范围": "精密注塑模具制造 业务手机 13800138000", "主营产品": "精密注塑模具",
         "官网": "https://huamo.example.com", "公开电话": "13800138000",
         "evidence": "宁波华模精密模具有限公司成立于 2010 年",
         "confidence": 0.9},
    ]


def bids_for(user_text: str):
    """名单页同时是一条中标公告，用来验证 bids.csv。"""
    if "list1" in user_text:
        return [{
            "企业名称": "宁波华模精密模具有限公司",
            "统一社会信用代码": "91330200TEST00001X",
            "项目名称": "2026 年宁波市北仑区精密模具采购项目",
            "采购方": "宁波市北仑区经济和信息化局",
            "中标金额": "1,280.5 万元",
            "公告日期": "2026-03-01",
            "evidence": "中标供应商：宁波华模精密模具有限公司，中标金额 1,280.5 万元",
        }]
    return []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send({"error": {"message": "bad json"}}, 400)

        if self.path.endswith("/v1/tools/search"):
            return self._send({"results": [
                {"rerank_score": 2.6, "title": "2026 年度浙江省注塑模具行业专精特新名单公示",
                 "url": "https://example.gov.cn/list1", "snippet": "名单公示", "summary": "名单公示",
                 "content": LIST_PAGE, "site_name": "example.gov.cn", "site_icon": None,
                 "date": "2026-03-01T00:00:00+00:00", "authority": 3},
                {"rerank_score": 2.2, "title": "宁波华模精密模具有限公司 - 官网",
                 "url": "https://huamo.example.com/about", "snippet": "关于我们", "summary": "关于我们",
                 "content": COMPANY_PAGE, "site_name": "huamo.example.com", "site_icon": None,
                 "date": "2025-11-02T00:00:00+00:00", "authority": 2},
            ]})
        if self.path.endswith("/v1/tools/fetch"):
            return self._send({"title": "mock fetch", "content": COMPANY_PAGE})
        if self.path.endswith("/chat/completions"):
            user = body.get("messages", [{}])[-1].get("content", "")
            content = json.dumps({"companies": companies_for(user),
                                  "bids": bids_for(user)}, ensure_ascii=False)
            return self._send({"choices": [{"message": {"role": "assistant", "content": content}}]})
        return self._send({"error": {"message": "not found"}}, 404)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
