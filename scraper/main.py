#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三站標案抓取器 v2.6
===================
1. 資策會：強化列表頁日期提取，掃描整行以尋找符合 YYY/MM/DD 格式的日期。
2. 資料合併優化：如果新舊資料都有案號，優先保留「日期欄位不為空」的那一筆。
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
KEEP_DAYS = 30
SINICA_LOOKBACK_DAYS = 3

SOURCES = ['工研院', '資策會', '中研院']
STATUS_FILE = 'docs/status.json'

# ============================================================================
# 日期處理工具
# ============================================================================

def format_to_roc(d):
    if not d or d == '-' or str(d).strip() == '': return "-"
    d_str = str(d).strip().replace('/', '-')
    if len(d_str) == 8 and d_str.isdigit():
        return f"{int(d_str[:4])-1911}/{d_str[4:6]}/{d_str[6:8]}"
    match = re.match(r'^(\d{2,4})-(\d{1,2})-(\d{1,2})', d_str)
    if match:
        y, m, day = int(match.group(1)), match.group(2).zfill(2), match.group(3).zfill(2)
        if y > 1900: y -= 1911
        return f"{y}/{m}/{day}"
    return str(d)

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
    return {'date': TODAY_CN, 'attempt': 0, 'sources': {src: {'status': 'pending', 'count': 0, 'time': '', 'error': ''} for src in SOURCES}, 'logs': []}

def save_status(status):
    os.makedirs('docs', exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f: json.dump(status, f, ensure_ascii=False, indent=2)

def add_log(status, msg):
    time_str = datetime.now(TW_TZ).strftime('%H:%M:%S')
    status['logs'].append({'time': time_str, 'msg': msg})
    print(f"[LOG {time_str}] {msg}")

# ============================================================================
# 資策會爬蟲 (v2.6 穩定提取日期)
# ============================================================================

def scrape_iii():
    print("[GET] 抓取資策會...")
    url = 'https://bid.iii.org.tw/bid/list/bid_new_list.aspx'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', id='GridView1')
        if not table: return pd.DataFrame()
        
        rows = table.find_all('tr')[1:21] # 前 20 筆
        bids = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 4: continue
            
            case_no = cols[0].text.strip()
            title = cols[2].text.strip()
            link_tag = cols[2].find('a')
            href_attr = link_tag.get('href', '') if link_tag else ''
            href = f"https://bid.iii.org.tw/bid/list/{href_attr.lstrip('/')}" if 'bid_no=' in href_attr else f"https://bid.iii.org.tw/bid/list/bid_new_list.aspx?bid_no={case_no}"
            
            # 策略：掃描整行所有欄位，找出所有符合 YYY/MM/DD 格式的日期
            found_dates = []
            for td in cols:
                txt = td.text.strip()
                # 匹配 115/03/06 或 2026/03/06
                m = re.search(r'(\d{2,4})/\d{1,2}/\d{1,2}', txt)
                if m: found_dates.append(txt)
            
            # 通常第一個日期是公告日，第二個是截止日
            pub_date = found_dates[0] if len(found_dates) > 0 else "-"
            end_date = found_dates[1] if len(found_dates) > 1 else "-"
            
            bids.append({
                '來源': '資策會', '案號': case_no, '標題': title, '標題連結': href,
                '預算金額': '', '公告日': pub_date, '截止日': end_date, '狀態': cols[1].text.strip(),
            })
        return pd.DataFrame(bids)
    except: return pd.DataFrame()

# ============================================================================
# 工研院爬蟲
# ============================================================================

def scrape_itri():
    print("[GET] 抓取工研院...")
    relay_url = "https://vendor.itri.org.tw/api/JsonRelayHandler.ashx"
    headers = {"Content-Type": "application/json; charset=UTF-8", "RemoteUrl": "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocList", "X-Requested-With": "XMLHttpRequest", "Referer": "https://vendor.itri.org.tw/broadBqry2.aspx"}
    payload = {"BidDocStatus": "", "IseBid": "", "currentPage": 1, "PageSize": 100}
    try:
        session = requests.Session(); session.get("https://vendor.itri.org.tw/broadBqry2.aspx", timeout=10)
        resp = session.post(relay_url, headers=headers, json=payload, timeout=TIMEOUT)
        data_obj = json.loads(resp.json()['Data'])
        bids = []
        for item in data_obj.get('Data', []):
            info = json.loads(item['bddata'])
            bid_info = info.get('BidInfo', {})
            case_no, seq = bid_info.get('CNo', ''), info.get('BidDocseqno', '')
            end_date = "-"
            ed = bid_info.get('EndDate', {})
            if isinstance(ed, dict) and ed.get('Year'):
                y = int(ed['Year'])
                if y > 1900: y -= 1911
                end_date = f"{y}/{str(ed['Month']).zfill(2)}/{str(ed['Day']).zfill(2)}"
            bids.append({
                '來源': '工研院', '案號': case_no, '標題': bid_info.get('CName', ''),
                '標題連結': f"https://vendor.itri.org.tw/broadBdetail2.aspx?seq={seq}",
                '預算金額': str(bid_info.get('Budget', bid_info.get('BudgetAmount', ''))),
                '公告日': info.get('LatestPublishdt', ''), '截止日': end_date, '狀態': info.get('BidDocStatus', ''),
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
                link = row.find('a', href=True); href = link['href'] if link else ''
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
# 主流程
# ============================================================================

def main():
    print(f"\n{'='*70}\n  三站標案抓取器 v2.6\n{'='*70}")
    status = load_status(); status['attempt'] += 1
    all_new_data = []
    for src in SOURCES:
        df = {'工研院': scrape_itri, '資策會': scrape_iii, '中研院': scrape_sinica}[src]()
        if not df.empty:
            all_new_data.append(df)
            status['sources'][src] = {'status': 'ok', 'count': len(df), 'time': datetime.now(TW_TZ).strftime('%H:%M:%S'), 'error': ''}
            add_log(status, f"{src}：成功，{len(df)} 筆")
        else: status['sources'][src]['status'] = 'fail'; add_log(status, f"{src}：無資料或失敗")

    new_df = pd.concat(all_new_data) if all_new_data else pd.DataFrame()
    latest_path = 'data/latest.csv'
    old_df = pd.read_csv(latest_path, dtype={'案號': str}) if os.path.exists(latest_path) else pd.DataFrame()
    
    # 【關鍵修正】合併策略：保留「公告日不為空」且「案號唯一」的標案
    final_df = pd.concat([new_df, old_df], ignore_index=True)
    if not final_df.empty:
        # 先按案號分組，對於每一組，優先挑選有日期的那一筆
        # 我們將 '-' 取代為 None 以便排序，然後排序並去重
        final_df['tmp_date'] = final_df['公告日'].replace('-', None)
        final_df = final_df.sort_values(by=['案號', 'tmp_date'], ascending=[True, False])
        final_df = final_df.drop_duplicates(subset=['案號'], keep='first').drop(columns=['tmp_date']).fillna('')
        
        # 篩選近 3 日
        def is_recent(d):
            try:
                d_str = str(d).strip().replace('/', '-')
                if len(d_str) == 8 and d_str.isdigit(): d_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
                if re.match(r'^\d{2,3}-', d_str):
                    pts = d_str.split('-'); d_str = f"{int(pts[0])+1911}-{pts[1]}-{pts[2]}"
                dt = datetime.strptime(d_str[:10], '%Y-%m-%d').replace(tzinfo=TW_TZ)
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
    save_status(status); print(f"\n[DONE] 執行完成，共 {len(final_df)} 筆。")

if __name__ == '__main__':
    main()
