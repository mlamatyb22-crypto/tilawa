#!/usr/bin/env python3
"""يبني ملف profiles.json: مدة كل آية (بالثواني) عند قارئين مرجعيين (الحصري والمنشاوي).

يحسب المدة من حجم ملف الصوت (طلب HEAD فقط، دون تنزيل الصوت)، فالملفات 128kbps ثابتة.
نستعمل هذه الأطوال كمرجع لإيقاع الآيات عند مطابقة وقفات القارئ الذي نقطّعه.
    python3 make_profile.py
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'profiles.json'
READERS = {'husary': 'ar.husary', 'minshawi': 'ar.minshawi'}
API = 'https://api.alquran.cloud/v1/surah/%d/%s'


def get(url, method='GET'):
    req = urllib.request.Request(url, method=method)
    return urllib.request.urlopen(req, timeout=40)


def size(url):
    for _ in range(3):
        try:
            return int(get(url, 'HEAD').headers['Content-Length'])
        except Exception:
            pass
    return None


def main():
    out = {}
    for name, ident in READERS.items():
        out[name] = {}
        urls = {}
        for s in range(1, 115):
            data = json.load(get(API % (s, ident)))['data']
            urls[s] = [a['audio'] for a in data['ayahs']]
        flat = [(s, i, u) for s, us in urls.items() for i, u in enumerate(us)]
        with ThreadPoolExecutor(16) as ex:
            sizes = list(ex.map(lambda t: size(t[2]), flat))
        if any(x is None for x in sizes):
            sys.exit(f'فشل جلب بعض الأحجام لـ {name}')
        for (s, i, _), b in zip(flat, sizes):
            out[name].setdefault(str(s), []).append(round(b * 8 / 128000, 2))
        print(name, 'ok', len(flat), flush=True)
    OUT.write_text(json.dumps(out), encoding='utf-8')


if __name__ == '__main__':
    main()
