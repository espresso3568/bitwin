#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三站標案抓取器 v2.1
===================
1. 工研院：修復 GetpublishDocDetail 解析邏輯，獲取精確截止日。
2. 資策會：強化預算抓取正則。
3. 全系統：統一民國日期格式 YYY/MM/DD。
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import os
import sys
import time
import re
import glob

# ============================================================================
# 全域設定
# ============================================================================

TW_TZ = ZoneInfo('Asia/Taipei')
NOW_TW = datetime.now(TW_TZ)
TODAY = NOW_TW.strftime('%Y%m%d')
TODAY_CN = NOW_TW.strftime('%Y-%m-%d')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

TIMEOUT = 30
MAX_RETRIES = 2
KEEP_DAYS = 30
SINICA_LOOKBACK_DAYS = 3

SOURCES = ['工研院', '資策會', '中研院']
STATUS_FILE = 'docs/status.json'

# ============================================================================
# 日期處理工具
# ============================================================================

def parse_date_to_iso(d):
    if not d: return None
    d = str(d).strip().replace('/', '-')
    if len(d) == 8 and d.isdigit(): return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    match_roc = re.match(r'^(\d{2,3})-(\d{2})-(\d{2})', d)
    if match_roc:
        y = int(match_roc.group(1)) + 1911
        return f"{y}-{match_roc.group(2)}-{match_roc.group(3)}"
    match_iso = re.match(r'^(\d{4})-(\d{2})-(\d{2})', d)
    if match_iso: return f"{match_iso.group(1)}-{match_iso.group(2)}-{match_iso.group(3)}"
    return None

def format_to_roc(d):
    iso = parse_date_to_iso(d)
    if not iso: return str(d) if d else "-"
    try:
        y, m, d = iso.split('-')
        return f"{int(y)-1911}/{m}/{d}"
    except: return str(d)

# ============================================================================
# 狀態管理
# ============================================================================

def load_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                status = json.load(f)
            if status.get('date') == TODAY_CN: return status
        except: pass
    return {
        'date': TODAY_CN, 'attempt': 0,
        'sources': {src: {'status': 'pending', 'count': 0, 'time': '', 'error': ''} for src in SOURCES},
        'logs': []
    }

def save_status(status):
    os.makedirs('docs', exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

def add_log(status, msg):
    time_str = datetime.now(TW_TZ).strftime('%H:%M:%S')
    status['logs'].append({'time': time_str, 'msg': msg})
    print(f"[LOG {time_str}] {msg}")

# ============================================================================
# 工研院爬蟲 (v2.1 修復版)
# ============================================================================

def scrape_itri():
    print("[GET] 抓取工研院...")
    relay_url = "https://vendor.itri.org.tw/api/JsonRelayHandler.ashx"
    
    session = requests.Session()
    session.get("https://vendor.itri.org.tw/broadBqry2.aspx", timeout=10)
    
    list_headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "RemoteUrl": "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocList",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://vendor.itri.org.tw/broadBqry2.aspx"
    }
    list_payload = {"BidDocStatus": "", "IseBid": "", "currentPage": 1, "PageSize": 100}

    try:
        resp = session.post(relay_url, headers=list_headers, json=list_payload, timeout=TIMEOUT)
        data_obj = json.loads(resp.json()['Data'])
        items = data_obj.get('Data', [])
        
        bids = []
        for item in items:
            info = json.loads(item['bddata'])
            bid_info = info.get('BidInfo', {})
            case_no = bid_info.get('CNo', '')
            seq = info.get('BidDocseqno', '')
            
            # 獲取截止日 (從詳情 API)
            end_date = "-"
            if seq:
                try:
                    detail_headers = list_headers.copy()
                    detail_headers["RemoteUrl"] = "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocDetail"
                    detail_payload = {"BidDocseqno": seq}
                    
                    time.sleep(0.3)
                    d_resp = session.post(relay_url, headers=detail_headers, json=detail_payload, timeout=10)
                    # 重要：詳情 API 直接回傳 JSON
                    detail_json = d_resp.json()
                    
                    # 優先從 ReceiveBidTerm 提取日期
                    term = detail_json.get('ReceiveBidTerm', '')
                    m = re.search(r'至\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', term)
                    if m:
                        end_date = f"{m.group(1)}/{m.group(2).zfill(2)}/{m.group(3).zfill(2)}"
                    else:
                        # 備案：使用原生的 EndDate 物件
                        ed = detail_json.get('EndDate', {})
                        if isinstance(ed, dict) and ed.get('Year'):
                            end_date = f"{ed['Year']}/{str(ed['Month']).zfill(2)}/{str(ed['Day']).zfill(2)}"
                except: pass

            bids.append({
                '來源': '工研院', '案號': case_no, '標題': bid_info.get('CName', ''),
                '標題連結': f"https://vendor.itri.org.tw/broadBdetail2.aspx?seq={seq}",
                '預算金額': str(bid_info.get('Budget', bid_info.get('BudgetAmount', ''))),
                '公告日': info.get('LatestPublishdt', ''),
                '截止日': end_date,
                '狀態': info.get('BidDocStatus', ''),
            })
        return pd.DataFrame(bids)
    except Exception as e:
        print(f"   [FAIL] 工研院錯誤: {e}")
        return pd.DataFrame()

# ============================================================================
# 資策會爬蟲
# ============================================================================

def scrape_iii():
    print("[GET] 抓取資策會...")
    url = 'https://bid.iii.org.tw/bid/list/bid_new_list.aspx'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', id='GridView1')
        if not table: return pd.DataFrame()
        
        rows = table.find_all('tr')[1:16]
        bids = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 8: continue

            # 資策會表格欄位：0:案號, 1:狀態, 2:標題, 3:類型... 招標次數可能包含在案號或獨立
            case_no_raw = cols[0].text.strip()
            # 通常案號格式為 PP26020052，次數在後方或隱含
            # 嘗試從連結中提取真正的 bid_no 與 ord
            link_tag = cols[2].find('a')
            href_attr = link_tag.get('href', '') if link_tag else ''

            # 建立正確的詳情連結
            if 'bid_no=' in href_attr:
                # 如果原始連結已有參數，直接使用並補全域名
                href = f"https://bid.iii.org.tw/bid/list/{href_attr.lstrip('/')}"
            else:
                # 否則手動構建 (保底方案)
                href = f"https://bid.iii.org.tw/bid/list/bid_new_list.aspx?bid_no={case_no_raw}"

            budget = ''
            try:
                time.sleep(0.5)
                # 使用正確的連結進入抓取預算
                d_resp = requests.get(href, headers=HEADERS, timeout=15)
                d_resp.encoding = 'utf-8'
                detail_text = d_resp.text
                m = re.search(r'預算金額\]\s*(?:新台幣|NT\$)?\s*([\d,]+)', detail_text)
                if m: budget = m.group(1).replace(',', '')
            except: pass

            bids.append({
                '來源': '資策會', '案號': case_no_raw, '標題': cols[2].text.strip(), '標題連結': href,
                '預算金額': budget, '公告日': cols[4].text.strip(), '截止日': cols[5].text.strip(), '狀態': cols[1].text.strip(),
            })

        return pd.DataFrame(bids)
    except: return pd.DataFrame()

# ============================================================================
# 中研院爬蟲
# ============================================================================

def scrape_sinica():
    print(f"[GET] 抓取中研院...")
    all_dfs = []
    for i in range(SINICA_LOOKBACK_DAYS):
        date = (NOW_TW - timedelta(days=i)).strftime('%Y-%m-%d')
        try:
            r = requests.get(f'https://srp.sinica.edu.tw/InviteBids?searchPubTime={date}', headers=HEADERS, timeout=TIMEOUT)
            soup = BeautifulSoup(r.text, 'html.parser')
            table = None
            for t in soup.find_all('table'):
                if '標' in t.get_text() or '採購' in t.get_text(): table = t; break
            if not table: continue
            
            headers = [th.text.strip() for th in table.find_all('th')]
            rows = table.find_all('tr')[1:]
            bids = []
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 4: continue
                link = row.find('a', href=True)
                href = link['href'] if link else ''
                if href and not href.startswith('http'): href = f"https://srp.sinica.edu.tw{href}"
                
                raw = {headers[idx]: col.text.strip() for idx, col in enumerate(cols) if idx < len(headers)}
                bid = {'來源': '中研院', '標題連結': href, '公告日': date}
                for k, v in raw.items():
                    if '案號' in k or '標號' in k: bid['案號'] = v
                    elif '名稱' in k: bid['標題'] = v
                    elif '預算' in k: 
                        m = re.search(r'([\d,]+)', v.replace('$', ''))
                        bid['預算金額'] = m.group(1).replace(',', '') if m else v
                    elif '截止' in k: bid['截止日'] = v
                bids.append(bid)
            if bids: all_dfs.append(pd.DataFrame(bids))
        except: continue
    if not all_dfs: return pd.DataFrame()
    return pd.concat(all_dfs).drop_duplicates(subset=['案號'], keep='first')

# ============================================================================
# 主程式
# ============================================================================

def main():
    print(f"\n{'='*70}\n  三站標案抓取器 v2.1\n{'='*70}")
    status = load_status()
    status['attempt'] += 1

    all_new_data = []
    for src in SOURCES:
        df = {'工研院': scrape_itri, '資策會': scrape_iii, '中研院': scrape_sinica}[src]()
        if not df.empty:
            all_new_data.append(df)
            status['sources'][src] = {'status': 'ok', 'count': len(df), 'time': datetime.now(TW_TZ).strftime('%H:%M:%S'), 'error': ''}
            add_log(status, f"{src}：成功，{len(df)} 筆")
        else:
            status['sources'][src]['status'] = 'fail'
            add_log(status, f"{src}：本次無新資料")

    new_df = pd.concat(all_new_data) if all_new_data else pd.DataFrame()
    latest_path = 'data/latest.csv'
    old_df = pd.read_csv(latest_path, dtype={'案號': str}) if os.path.exists(latest_path) else pd.DataFrame()
    
    final_df = pd.concat([new_df, old_df], ignore_index=True)
    if not final_df.empty:
        final_df = final_df.drop_duplicates(subset=['案號'], keep='first').fillna('')
        
        def is_recent(d):
            try:
                iso = parse_date_to_iso(d)
                if not iso: return True
                dt = datetime.strptime(iso, '%Y-%m-%d').replace(tzinfo=TW_TZ)
                return dt >= (datetime.now(TW_TZ) - timedelta(days=2)).replace(hour=0, minute=0, second=0)
            except: return True
        
        final_df = final_df[final_df['公告日'].apply(is_recent)]
        final_df['公告日'] = final_df['公告日'].apply(format_to_roc)
        final_df['截止日'] = final_df['截止日'].apply(format_to_roc)

    os.makedirs('data', exist_ok=True)
    final_df.to_csv(latest_path, index=False, encoding='utf-8-sig')
    final_df.to_csv(f'data/三站標案_{TODAY}.csv', index=False, encoding='utf-8-sig')
    
    res_json = {
        'update_time': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        'date': TODAY_CN, 'total': len(final_df),
        'sources': {s: len(final_df[final_df['來源']==s]) for s in final_df['來源'].unique()} if not final_df.empty else {},
        'data': final_df.to_dict('records') if not final_df.empty else []
    }
    with open('docs/data.json', 'w', encoding='utf-8') as f: json.dump(res_json, f, ensure_ascii=False, indent=2)
    with open(f'docs/data_{TODAY}.json', 'w', encoding='utf-8') as f: json.dump(res_json, f, ensure_ascii=False, indent=2)

    save_status(status)
    print(f"\n[DONE] 執行完成，共 {len(final_df)} 筆。")

if __name__ == '__main__':
    main()
